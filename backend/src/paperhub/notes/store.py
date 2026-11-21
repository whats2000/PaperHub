"""Note + block store helpers (read side and revision counter)."""
from __future__ import annotations

import json
import secrets
import string
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from paperhub.db.connection import in_write_transaction, write_lock
from paperhub.notes.models import Block, Note, table_block_text, table_rows
from paperhub.notes.run_text import link_text

_ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_block_id() -> str:
    return "b_" + "".join(secrets.choice(_ALPHABET) for _ in range(10))


def _runs_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for run in content:
        if not isinstance(run, dict):
            continue
        if isinstance(run.get("text"), str):
            out.append(run["text"])
        elif run.get("type") == "inlineMath":  # inline math: its LaTeX
            out.append(str((run.get("props") or {}).get("latex") or ""))
        elif run.get("type") == "markCite":  # an inline citation: its alias
            out.append(str((run.get("props") or {}).get("text") or ""))
        elif run.get("type") == "noteLink":  # a [[ link: label, else name
            out.append(link_text(run.get("props") or {}))
        if run.get("content") is not None:  # links nest their runs
            out.append(_runs_text(run["content"]))
    return "".join(out)


def block_text(type: str, props: dict[str, Any], content: Any) -> str:
    """Plain text of a block, as indexed for search and read by the agent."""
    if type == "section":  # the paper section's name
        return str(props.get("title") or "")
    if type == "paperQuote":
        return str(props.get("quote") or "")
    if type == "math":
        return str(props.get("latex") or "")
    if type == "table" and isinstance(content, dict):
        return table_block_text(props, "\n".join(
            " | ".join(_runs_text(c) for c in row) for row in table_rows(content)))
    if type == "codeBlock":
        return _runs_text(content) or str(props.get("code") or "")
    return _runs_text(content)


def _note(row: Any) -> Note:
    return Note(id=row[0], target_kind=row[1], target_id=row[2], title=row[3],
                revision=row[4], updated_at=row[5], deleted_at=row[6])


_NOTE_COLS = "id, target_kind, target_id, title, revision, updated_at, deleted_at"


_SHARED_BY_SQL = {
    # A paper's note is shared by every chat that attaches the paper.
    "paper": "SELECT COUNT(DISTINCT p.session_id) FROM papers p "
             "JOIN chat_sessions s ON s.id = p.session_id "
             "WHERE p.paper_content_id = ? AND s.deleted_at IS NULL "
             "AND s.internal = 0",
    "session": "SELECT COUNT(*) FROM chat_sessions WHERE id = ? "
               "AND deleted_at IS NULL AND internal = 0",
    "idea": "SELECT COUNT(DISTINCT i.session_id) FROM idea_sessions i "
            "JOIN chat_sessions s ON s.id = i.session_id "
            "WHERE i.idea_id = ? AND s.deleted_at IS NULL AND s.internal = 0",
}


async def shared_by(conn: aiosqlite.Connection, note: Note) -> int:
    """How many live chats the note is linked to (a paper note: the chats
    attaching its paper; a chat note: its chat; an idea note: its chats)."""
    sql = _SHARED_BY_SQL.get(note.target_kind)
    if sql is None or not note.target_id.isdigit():
        return 0
    async with conn.execute(sql, (int(note.target_id),)) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


_DEFAULT_TITLE_SQL = {
    "paper": "SELECT title FROM paper_content WHERE id = ?",
    "session": "SELECT title FROM chat_sessions WHERE id = ?",
    "idea": "SELECT title FROM ideas WHERE id = ?",
}

UNTITLED = "Untitled"


async def default_title(conn: aiosqlite.Connection, note: Note) -> str:
    """The title a note shows when its own is empty: its target's current
    title (paper / chat / idea), or "" when that is empty or gone."""
    sql = _DEFAULT_TITLE_SQL.get(note.target_kind)
    if sql is None or not note.target_id.isdigit():
        return ""
    async with conn.execute(sql, (int(note.target_id),)) as cur:
        row = await cur.fetchone()
    return str(row[0] or "").strip() if row else ""


async def _target_exists(conn: aiosqlite.Connection, note: Note) -> bool:
    sql = _DEFAULT_TITLE_SQL.get(note.target_kind)
    if sql is None or not note.target_id.isdigit():
        return False
    async with conn.execute(sql, (int(note.target_id),)) as cur:
        return await cur.fetchone() is not None


# Block types that hold something with no text of their own.
_CONTENT_TYPES = ("paperFigure", "paperRegion", "paperEquation")

# A note "has content" (SQL over ``notes n``): the user's own title (not
# the target's), or a live
# block with text (or a figure / region / equation). Shared by ``[[`` search,
# the sidebar's chat list and ``paperhub-clean-empty-chats``.
# A title equal to the target's own (auto-filled before
# ``paperhub-clear-auto-note-titles``) is not the user's: it does not count.
_TARGET_TITLE_SQL = (
    "COALESCE(CASE n.target_kind "
    "WHEN 'session' THEN (SELECT TRIM(t.title) FROM chat_sessions t "
    "WHERE CAST(t.id AS TEXT) = n.target_id) "
    "WHEN 'paper' THEN (SELECT TRIM(t.title) FROM paper_content t "
    "WHERE CAST(t.id AS TEXT) = n.target_id) "
    "WHEN 'idea' THEN (SELECT TRIM(t.title) FROM ideas t "
    "WHERE CAST(t.id AS TEXT) = n.target_id) END, '')"
)

NOTE_HAS_CONTENT_SQL = (
    "((TRIM(n.title) <> '' AND TRIM(n.title) <> " + _TARGET_TITLE_SQL + ") "
    "OR EXISTS (SELECT 1 FROM note_blocks b "
    "WHERE b.note_id = n.id AND b.deleted_at IS NULL AND (TRIM(b.text) <> '' "
    f"OR b.type IN ({', '.join(repr(t) for t in _CONTENT_TYPES)}))))"
)

# A chat note whose chat the sidebar does not list (gone, deleted, internal).
_HIDDEN_CHAT_SQL = (
    "(n.target_kind = 'session' AND NOT EXISTS (SELECT 1 FROM chat_sessions s "
    "WHERE CAST(s.id AS TEXT) = n.target_id AND s.deleted_at IS NULL "
    "AND s.internal = 0))"
)


def _link_targets(node: Any, out: set[int]) -> None:
    if isinstance(node, list):
        for item in node:
            _link_targets(item, out)
    elif isinstance(node, dict):
        if node.get("type") == "noteLink":
            props = node.get("props") or {}
            tid = props.get("targetId") if isinstance(props, dict) else None
            if (props.get("targetKind") == "note" and isinstance(tid, int)
                    and not isinstance(tid, bool)):
                out.add(tid)
        for value in node.values():
            if isinstance(value, (list, dict)):
                _link_targets(value, out)


async def linked_note_ids(conn: aiosqlite.Connection) -> set[int]:
    """Ids of the notes some live ``[[`` link (in a live block of a live
    note, a table cell included) points at."""
    async with conn.execute(
        "SELECT b.payload_json FROM note_blocks b JOIN notes n ON n.id = b.note_id "
        "WHERE b.deleted_at IS NULL AND n.deleted_at IS NULL "
        "AND b.payload_json LIKE '%noteLink%'"
    ) as cur:
        rows = await cur.fetchall()
    out: set[int] = set()
    for (payload,) in rows:
        try:
            _link_targets(json.loads(payload), out)
        except ValueError:
            continue
    return out


async def find_note(conn: aiosqlite.Connection, *, target_kind: str,
                    target_id: str) -> Note | None:
    """The target's note when it has one (soft-deleted too), else None:
    looking never creates one."""
    async with conn.execute(
        f"SELECT {_NOTE_COLS} FROM notes "
        "WHERE owner_id = 1 AND target_kind = ? AND target_id = ?",
        (target_kind, target_id),
    ) as cur:
        row = await cur.fetchone()
    return _note(row) if row else None


async def list_live_notes(
    conn: aiosqlite.Connection, query: str = "", *, limit: int | None = None,
) -> list[tuple[Note, str]]:
    """Live notes of the owner as ``(note, display title)``, most recently
    updated first, whose title contains ``query`` (case-insensitive).

    Left out: an orphan (target gone, no title of its own), an empty note
    (no title, no live block with text) and the note of a chat the sidebar
    does not list -- unless some live ``[[`` link points at the note."""
    needle = query.strip().casefold()
    async with conn.execute(
        f"SELECT {_NOTE_COLS}, {NOTE_HAS_CONTENT_SQL}, {_HIDDEN_CHAT_SQL} "  # noqa: S608
        "FROM notes n WHERE owner_id = 1 AND deleted_at IS NULL "
        "ORDER BY updated_at DESC, id DESC"
    ) as cur:
        rows = await cur.fetchall()
    linked: set[int] | None = None
    out: list[tuple[Note, str]] = []
    for row in rows:
        note = _note(row)
        if not row[7] or row[8]:
            if linked is None:
                linked = await linked_note_ids(conn)
            if note.id not in linked:
                continue
        if not note.title.strip() and not await _target_exists(conn, note):
            continue
        title = await display_title(conn, note)
        if needle in title.casefold():
            out.append((note, title))
            if limit is not None and len(out) >= limit:
                break
    return out


async def note_first_line(conn: aiosqlite.Connection, note_id: int) -> str:
    """What a note-only chat is called in the sidebar: the note's own
    title, else the first line of its first block with text ("" if none)."""
    note = await get_note(conn, note_id)
    if note is None:
        return ""
    own = " ".join(note.title.split())
    if own and own != " ".join((await default_title(conn, note)).split()):
        return own
    for b in await list_blocks(conn, note_id):
        for line in b.text.splitlines():
            if line.strip():
                return " ".join(line.split())[:200]
    return ""


async def named(conn: aiosqlite.Connection, note: Note) -> Note:
    """``note`` with ``title`` set to its display title (for readers that
    name it; never stored back)."""
    return note.model_copy(update={"title": await display_title(conn, note)})


async def display_title(conn: aiosqlite.Connection, note: Note) -> str:
    """The note's name everywhere it is named (export, lists, the agent):
    the user's title, else the default title, else "Untitled"."""
    return (note.title.strip() or await default_title(conn, note)
            or UNTITLED)


async def set_title(conn: aiosqlite.Connection, note_id: int,
                    title: str) -> Note | None:
    """Rename a note (the page title). The block revision is untouched: a
    title is not a block, so pending block ops keep their base revision."""
    async with write_lock(conn):
        cur = await conn.execute(
            "UPDATE notes SET title = ?, updated_at = ? WHERE id = ?",
            (" ".join(title.split()), _now(), note_id))
        if not in_write_transaction():
            await conn.commit()
    if cur.rowcount == 0:
        return None
    return await get_note(conn, note_id)


async def get_note(conn: aiosqlite.Connection, note_id: int) -> Note | None:
    async with conn.execute(
        f"SELECT {_NOTE_COLS} FROM notes WHERE id = ?", (note_id,)
    ) as cur:
        row = await cur.fetchone()
    return _note(row) if row else None


async def get_or_create_note(
    conn: aiosqlite.Connection, *, target_kind: str, target_id: str,
    title: str, session_id: int | None = None,
) -> Note:
    """The (single-owner) note for a target, created on first use.

    A soft-deleted note is returned as-is with ``deleted_at`` set; the caller
    decides (the chat-note lifecycle restores it).
    """
    now = _now()
    async with write_lock(conn):
        await conn.execute(
            "INSERT INTO notes (target_kind, target_id, session_id, title, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (owner_id, target_kind, target_id) DO NOTHING",
            (target_kind, target_id, session_id, title, now, now),
        )
        if not in_write_transaction():
            await conn.commit()
    async with conn.execute(
        f"SELECT {_NOTE_COLS} FROM notes "
        "WHERE owner_id = 1 AND target_kind = ? AND target_id = ?",
        (target_kind, target_id),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return _note(row)


async def list_blocks(
    conn: aiosqlite.Connection, note_id: int, *, include_deleted: bool = False,
) -> list[Block]:
    """Blocks in document order: depth-first, siblings by ``order_key``.

    Deleting a block soft-deletes its whole subtree (the op engine relies on
    it). Should a live block still have a missing / deleted parent, it is
    surfaced after the tree (in ``order_key`` order) rather than hidden.
    """
    where = "note_id = ?" + ("" if include_deleted else " AND deleted_at IS NULL")
    async with conn.execute(
        "SELECT id, note_id, parent_id, type, payload_json, text, author, "
        f"section_key, changed_rev, order_key FROM note_blocks WHERE {where}",
        (note_id,),
    ) as cur:
        rows = await cur.fetchall()
    return order_block_rows(list(rows))


def order_block_rows(rows: Sequence[Sequence[Any]]) -> list[Block]:
    """``note_blocks`` rows (id, note_id, parent_id, type, payload_json, text,
    author, section_key, changed_rev, order_key) of ONE note, as blocks in
    document order."""
    children: dict[str | None, list[Any]] = {}
    for r in rows:
        children.setdefault(r[2], []).append(r)
    for sibs in children.values():
        sibs.sort(key=lambda r: (r[9], r[0]))
    ids = {r[0] for r in rows}
    orphans = sorted(
        (r for r in rows if r[2] is not None and r[2] not in ids),
        key=lambda r: (r[9], r[0]),
    )
    out: list[Block] = []
    stack = list(reversed(orphans)) + list(reversed(children.get(None, [])))
    while stack:
        r = stack.pop()
        payload = json.loads(r[4])
        out.append(Block(
            id=r[0], note_id=r[1], parent_id=r[2], type=r[3],
            props=payload.get("props") or {}, content=payload.get("content"),
            text=r[5], author=r[6], section_key=r[7], changed_rev=r[8],
        ))
        stack.extend(reversed(children.get(r[0], [])))
    return out


async def bump_revision(
    conn: aiosqlite.Connection, note_id: int, *, actor: str, summary: str,
    change_set_id: str | None = None, run_id: int | None = None,
) -> int:
    """Advance the note's revision and record it; returns the new revision."""
    now = _now()
    async with write_lock(conn):
        async with conn.execute(
            "UPDATE notes SET revision = revision + 1, updated_at = ? "
            "WHERE id = ? RETURNING revision", (now, note_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise LookupError(f"note {note_id} not found")
        rev = int(row[0])
        await conn.execute(
            "INSERT INTO note_revisions (note_id, revision, actor, summary, "
            "change_set_id, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (note_id, rev, actor, summary, change_set_id, run_id, now),
        )
        if not in_write_transaction():
            await conn.commit()
    return rev
