"""Fork a chat session at a chosen message (SRS v2.30).

``fork_session`` branches a NEW chat session from the point ABOVE a forked
user message: it copies every message STRICTLY BEFORE that message (remapping
each turn's ``run_id`` to a fresh ``runs`` row carrying the per-turn replay
data), the session's ``papers`` membership rows (same shared ``paper_content``),
the active session-scoped ``memories``, and the ``slide_style_overrides`` row.
The forked message itself + everything after it are NOT copied — the message
text is returned for the composer prefill. The dev-only ``tool_calls`` trace is
NOT copied (observability, not user history).

The deck is copied by ``_copy_deck`` (Task 2), AFTER the core transaction commits
and best-effort: a deck-artifact copy failure leaves the fork deckless rather
than aborting it.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from paperhub.db.connection import write_transaction

_LOG = logging.getLogger(__name__)

_FORK_TITLE_PREFIX = "Fork of "


@dataclass(frozen=True)
class ForkResult:
    new_session_id: int
    forked_message: str
    title: str


async def _forked_message(
    conn: aiosqlite.Connection, *, source_session_id: int, fork_run_id: int
) -> tuple[int, str]:
    """Return (message_id, content) of the forked user message — the earliest
    user message of ``fork_run_id`` in the source session. Raises ValueError if
    no such message exists (a bad run_id, or a run with no user message)."""
    async with conn.execute(
        "SELECT id, content FROM messages "
        "WHERE session_id = ? AND run_id = ? AND role = 'user' "
        "ORDER BY id LIMIT 1",
        (source_session_id, fork_run_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError(
            f"no user message for run_id={fork_run_id} in session {source_session_id}"
        )
    return int(row[0]), str(row[1])


async def fork_session(
    conn: aiosqlite.Connection,
    *,
    source_session_id: int,
    fork_run_id: int,
    workspace_dir: Path,  # used by the deck copy added in Task 2
) -> ForkResult:
    # Resolve the fork point first (raises if the run_id is bogus).
    fork_msg_id, forked_text = await _forked_message(
        conn, source_session_id=source_session_id, fork_run_id=fork_run_id
    )

    async with conn.execute(
        "SELECT title FROM chat_sessions WHERE id = ?", (source_session_id,)
    ) as cur:
        srow = await cur.fetchone()
    if srow is None:
        raise ValueError(f"source session {source_session_id} not found")
    new_title = f"{_FORK_TITLE_PREFIX}{srow[0]}"

    # --- Core copy: atomic. -------------------------------------------------
    async with write_transaction(conn):
        cur = await conn.execute(
            "INSERT INTO chat_sessions (title, forked_from_session_id) "
            "VALUES (?, ?)",
            (new_title, source_session_id),
        )
        assert cur.lastrowid is not None
        new_sid = int(cur.lastrowid)

        # Messages strictly before the fork point, in id order.
        async with conn.execute(
            "SELECT id, role, content, run_id, created_at FROM messages "
            "WHERE session_id = ? AND id < ? ORDER BY id",
            (source_session_id, fork_msg_id),
        ) as mcur:
            msg_rows = await mcur.fetchall()

        # Remap each distinct old run_id -> a fresh run row (preserving the
        # replay payload). NULL run_ids stay NULL.
        run_map: dict[int, int] = {}
        for _mid, _role, _content, old_run_id, _created in msg_rows:
            if old_run_id is None or old_run_id in run_map:
                continue
            async with conn.execute(
                "SELECT routing_decision_json, search_results_json, "
                "deck_version_id, started_at, finished_at, status "
                "FROM runs WHERE id = ?",
                (old_run_id,),
            ) as rcur:
                r = await rcur.fetchone()
            if r is None:
                continue
            ins = await conn.execute(
                "INSERT INTO runs (session_id, routing_decision_json, "
                "search_results_json, deck_version_id, started_at, finished_at, "
                "status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_sid, r[0], r[1], r[2], r[3], r[4], r[5]),
            )
            assert ins.lastrowid is not None
            run_map[int(old_run_id)] = int(ins.lastrowid)

        for _mid, role, content, old_run_id, created in msg_rows:
            new_run_id = run_map.get(old_run_id) if old_run_id is not None else None
            await conn.execute(
                "INSERT INTO messages (session_id, role, content, run_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (new_sid, role, content, new_run_id, created),
            )

        # papers membership: same shared paper_content, preserve enabled+added_at.
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled, added_at) "
            "SELECT ?, paper_content_id, enabled, added_at FROM papers "
            "WHERE session_id = ?",
            (new_sid, source_session_id),
        )

        # Active session memories, re-scoped, chain FKs reset (fresh chain).
        await conn.execute(
            "INSERT INTO memories (scope, session_id, content, status, metadata) "
            "SELECT 'session', ?, content, 'active', metadata FROM memories "
            "WHERE session_id = ? AND scope = 'session' AND status = 'active'",
            (new_sid, source_session_id),
        )

        # slide_style_overrides (per-session deck style) — at most one row.
        await conn.execute(
            "INSERT INTO slide_style_overrides "
            "(session_id, preamble_tex, source) "
            "SELECT ?, preamble_tex, source FROM slide_style_overrides "
            "WHERE session_id = ?",
            (new_sid, source_session_id),
        )

    await _copy_deck(
        conn,
        source_session_id=source_session_id,
        new_session_id=new_sid,
        workspace_dir=workspace_dir,
        fork_run_id=fork_run_id,
    )

    return ForkResult(
        new_session_id=new_sid, forked_message=forked_text, title=new_title
    )


def _copy_slides_tree(src: Path, dst: Path, *, new_session_id: int) -> bool:
    """Blocking copy of a session's ``slides/`` artifact dir. Returns True on a
    successful copy, False if the source is missing or the copy fails
    (best-effort — the caller leaves the fork deckless). Run via
    ``asyncio.to_thread`` so the event loop is not stalled during the copy.
    """
    try:
        if not src.exists():
            _LOG.warning(
                "fork: source slides dir %s missing; fork %s left deckless",
                src, new_session_id,
            )
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True
    except OSError as exc:
        _LOG.warning(
            "fork: deck-artifact copy failed (%r); fork %s left deckless",
            exc, new_session_id,
        )
        return False


async def _copy_deck(
    conn: aiosqlite.Connection,
    *,
    source_session_id: int,
    new_session_id: int,
    workspace_dir: Path,
    fork_run_id: int,
) -> None:
    """Best-effort: copy the source deck (decks + deck_slides rows + the whole
    slides/ artifact dir) into the fork. On ANY failure, leave the fork deckless
    — never raise, so a deck problem can't abort an otherwise-good fork.

    Only copies the deck if it existed AS OF the fork point: a fork branches the
    conversation ABOVE ``fork_run_id``, so a deck produced BY that turn (or a
    later one) did not exist at the branch point and must NOT be carried over —
    otherwise forking above the very turn that GENERATED the deck wrongly ships a
    "future" deck the branch never had (and re-running it routes to EDIT instead
    of a fresh generate). A deck-producing run stamps ``runs.deck_version_id``;
    copy only when some run STRICTLY BEFORE the fork point did so."""
    async with conn.execute(
        "SELECT 1 FROM runs WHERE session_id = ? AND id < ? "
        "AND deck_version_id IS NOT NULL LIMIT 1",
        (source_session_id, fork_run_id),
    ) as cur:
        deck_existed_at_fork_point = await cur.fetchone() is not None
    if not deck_existed_at_fork_point:
        return  # deck was generated AT/AFTER the fork point — branch is deckless

    async with conn.execute(
        "SELECT id, run_id, tex_path, pdf_path, speaker_notes_json, plan_json, "
        "page_count, current_version_id, contributing_paper_ids_json, status "
        "FROM decks WHERE session_id = ?",
        (source_session_id,),
    ) as cur:
        deck = await cur.fetchone()
    if deck is None:
        return  # no deck to copy

    src_slides = workspace_dir / "chat_session" / str(source_session_id) / "slides"
    dst_slides = workspace_dir / "chat_session" / str(new_session_id) / "slides"

    # Copy the artifact tree FIRST (outside the DB write lock — it can be slow).
    # Offloaded to a thread so the event loop is not stalled during the copy.
    # If the source dir is missing or the copy fails, bail without inserting
    # deck rows (deckless fork).
    copied = await asyncio.to_thread(
        _copy_slides_tree, src_slides, dst_slides, new_session_id=new_session_id
    )
    if not copied:
        return

    # Rewrite the absolute tex/pdf paths to point into the fork's own dir.
    new_tex = str(dst_slides / "deck.tex")
    new_pdf = str(dst_slides / "deck.pdf") if deck[3] else None

    try:
        async with write_transaction(conn):
            await conn.execute(
                "INSERT INTO decks (session_id, run_id, tex_path, pdf_path, "
                "speaker_notes_json, plan_json, page_count, current_version_id, "
                "contributing_paper_ids_json, status) "
                "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_session_id, new_tex, new_pdf, deck[4], deck[5], deck[6], deck[7],
                 deck[8], deck[9]),
            )
            async with conn.execute(
                "SELECT id FROM decks WHERE session_id = ?", (new_session_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise RuntimeError(
                    f"_copy_deck: decks row missing after INSERT for session {new_session_id}"
                )
            new_deck_id = int(row[0])

            await conn.execute(
                "INSERT INTO deck_slides (deck_id, slide_index, frame_tex, note_text, "
                "note_language, page_start, page_end, source_sections_json) "
                "SELECT ?, slide_index, frame_tex, note_text, note_language, "
                "page_start, page_end, source_sections_json "
                "FROM deck_slides WHERE deck_id = ?",
                (new_deck_id, deck[0]),
            )
    except Exception as exc:  # noqa: BLE001 — best-effort; a deck failure must not abort the fork
        _LOG.warning(
            "fork: deck-row copy failed (%r); fork %s left deckless",
            exc, new_session_id,
        )
        return
