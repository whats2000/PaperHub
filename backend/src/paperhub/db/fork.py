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

from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from paperhub.db.connection import write_transaction

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
            "INSERT INTO chat_sessions (title) VALUES (?)", (new_title,)
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

    # Deck copy (best-effort) is added in Task 2.

    return ForkResult(
        new_session_id=new_sid, forked_message=forked_text, title=new_title
    )
