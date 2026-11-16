"""An idea's free-text note, kept in the notes store (``target_kind='idea'``).

``ideas.note`` is the pre-H5b.1 home; the column stays for one release (an old
client's ``PATCH`` still sends it) but the note's text is read from here.
"""
from __future__ import annotations

import re

import aiosqlite

from paperhub.notes.models import Note
from paperhub.notes.ops import Actor, BlockIn, Op, _apply_in_tx
from paperhub.notes.papers import tx
from paperhub.notes.store import block_text, get_or_create_note, list_blocks

_BLANK = re.compile(r"\n\s*\n")


def paragraphs(text: str) -> list[str]:
    """The non-empty paragraphs of free text (split on blank lines)."""
    return [p.strip() for p in _BLANK.split(text or "") if p.strip()]


async def _note(conn: aiosqlite.Connection, idea_id: int) -> Note | None:
    async with conn.execute(
        "SELECT id, target_kind, target_id, title, revision, updated_at, deleted_at "
        "FROM notes WHERE owner_id = 1 AND target_kind = 'idea' AND target_id = ? "
        "AND deleted_at IS NULL", (str(idea_id),),
    ) as cur:
        r = await cur.fetchone()
    return Note(id=r[0], target_kind=r[1], target_id=r[2], title=r[3],
                revision=r[4], updated_at=r[5], deleted_at=r[6]) if r else None


async def idea_note_text_or_none(conn: aiosqlite.Connection, idea_id: int) -> str | None:
    """The idea note's text (block texts joined by blank lines), or ``None``
    when the idea has no note in the store yet (not migrated)."""
    note = await _note(conn, idea_id)
    if note is None:
        return None
    blocks = await list_blocks(conn, note.id)
    texts = [(b.text or block_text(b.type, b.props, b.content)).strip() for b in blocks]
    return "\n\n".join(t for t in texts if t)


async def idea_note_text(conn: aiosqlite.Connection, idea_id: int) -> str:
    return await idea_note_text_or_none(conn, idea_id) or ""


async def set_idea_note_text(
    conn: aiosqlite.Connection, idea_id: int, text: str, *, actor: Actor,
    summary: str = "",
) -> None:
    """Make the idea note's paragraphs ``text``'s paragraphs: one revision
    (delete the old paragraph blocks, append the new ones), atomic with the
    caller's transaction when it is inside one."""
    async with conn.execute("SELECT title FROM ideas WHERE id = ?", (idea_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"idea {idea_id} not found")
    new = paragraphs(text)
    async with tx(conn):
        note = await get_or_create_note(
            conn, target_kind="idea", target_id=str(idea_id), title=str(row[0]))
        old = [b for b in await list_blocks(conn, note.id) if b.type == "paragraph"]
        if not old and not new:
            return
        ops: list[Op] = [Op(op="delete", block_id=b.id) for b in old]
        ops += [Op(op="insert", block=BlockIn(
            type="paragraph", content=[{"type": "text", "text": p, "styles": {}}]))
            for p in new]
        await _apply_in_tx(conn, note.id, base_revision=note.revision, ops=ops,
                           actor=actor, run_id=None, summary=summary)


