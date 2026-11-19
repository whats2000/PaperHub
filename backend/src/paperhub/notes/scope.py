"""Which notes a chat can see, and how the agent names them.

Refs: ``chat`` (this chat's note), ``paper:<paper_content_id>``,
``idea:<id>``, ``note:<id>`` (any note, by id).
"""
from __future__ import annotations

import aiosqlite

from paperhub.notes.models import Note
from paperhub.notes.papers import paper_note
from paperhub.notes.store import get_note, get_or_create_note, named

_NOTE_COLS = "id, target_kind, target_id, title, revision, updated_at, deleted_at"


def _row(r: aiosqlite.Row | tuple) -> Note:  # type: ignore[type-arg]
    return Note(id=r[0], target_kind=r[1], target_id=r[2], title=r[3],
                revision=r[4], updated_at=r[5], deleted_at=r[6])


def ref_of(note: Note, session_id: int) -> str:
    """The ref the agent uses for ``note`` in chat ``session_id``."""
    if note.target_kind == "session" and note.target_id == str(session_id):
        return "chat"
    if note.target_kind in ("paper", "idea"):
        return f"{note.target_kind}:{note.target_id}"
    return f"note:{note.id}"


async def _chat_note(conn: aiosqlite.Connection, session_id: int) -> Note:
    async with conn.execute(
        "SELECT title FROM chat_sessions WHERE id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"chat {session_id} not found")
    return await get_or_create_note(
        conn, target_kind="session", target_id=str(session_id),
        title="", session_id=session_id)


async def _idea_note(conn: aiosqlite.Connection, idea_id: int) -> Note:
    async with conn.execute("SELECT title FROM ideas WHERE id = ?", (idea_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"idea {idea_id} not found")
    return await get_or_create_note(
        conn, target_kind="idea", target_id=str(idea_id), title="")


async def notes_in_scope(
    conn: aiosqlite.Connection, session_id: int, *, everything: bool = False,
    paper_ids: list[int] | None = None,
) -> list[Note]:
    """The chat's note, the notes of its enabled papers and, in an idea chat,
    the idea's note (created on first use); with ``everything`` every live
    note. ``paper_ids`` (the turn's enabled papers) overrides the lookup. Soft-deleted notes are left out."""
    if everything:
        async with conn.execute(
            f"SELECT {_NOTE_COLS} FROM notes "  # noqa: S608
            "WHERE owner_id = 1 AND deleted_at IS NULL "
            "ORDER BY updated_at DESC, id DESC"
        ) as cur:
            return [await named(conn, _row(r)) for r in await cur.fetchall()]
    notes = [await _chat_note(conn, session_id)]
    if paper_ids is None:
        async with conn.execute(
            "SELECT p.paper_content_id FROM papers p WHERE p.session_id = ? "
            "AND p.enabled = 1 ORDER BY p.added_at, p.id", (session_id,)
        ) as cur:
            paper_ids = [int(r[0]) for r in await cur.fetchall()]
    notes += [await paper_note(conn, pid) for pid in dict.fromkeys(paper_ids)]
    async with conn.execute(
        "SELECT idea_id FROM idea_sessions WHERE session_id = ? "
        "ORDER BY idea_id DESC LIMIT 1", (session_id,)
    ) as cur:
        idea = await cur.fetchone()
    if idea is not None:
        notes.append(await _idea_note(conn, int(idea[0])))
    # Notes are named by their display title (the user's, else the default).
    return [await named(conn, n) for n in notes if n.deleted_at is None]


async def note_ref(conn: aiosqlite.Connection, ref: str, session_id: int) -> Note:
    """Resolve an agent-facing ref to a live note (paper / chat / idea notes
    are created on demand). Raises ``ValueError`` for a malformed ref and
    ``LookupError`` for a missing or deleted note."""
    ref = ref.strip()
    kind, _, rest = ref.partition(":")
    if ref == "chat":
        note = await _chat_note(conn, session_id)
    elif kind in ("paper", "idea", "note") and rest.isdigit():
        n = int(rest)
        if kind == "paper":
            note = await paper_note(conn, n)
        elif kind == "idea":
            note = await _idea_note(conn, n)
        else:
            found = await get_note(conn, n)
            if found is None:
                raise LookupError(f"note {n} not found")
            note = found
    else:
        raise ValueError(
            f"Unknown note ref {ref!r}; use 'chat', 'paper:<id>', 'idea:<id>' "
            "or 'note:<id>' (list_notes shows the refs).")
    if note.deleted_at is not None:
        raise LookupError(f"{ref} was deleted")
    return await named(conn, note)
