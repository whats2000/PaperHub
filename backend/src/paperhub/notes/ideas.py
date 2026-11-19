"""An idea's free-text note, kept in the notes store (``target_kind='idea'``).

``ideas.note`` is the pre-H5b.1 home; the column stays for one release (an old
client's ``PATCH`` still sends it) but the note's text is read from here.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import aiosqlite

from paperhub.notes.models import Note
from paperhub.notes.ops import Actor, BlockIn, Op, _apply_in_tx
from paperhub.notes.papers import tx
from paperhub.notes.store import (
    block_text,
    get_or_create_note,
    list_blocks,
    order_block_rows,
)

_BLANK = re.compile(r"\n\s*\n")


def paragraphs(text: str) -> list[str]:
    """The non-empty paragraphs of free text (split on blank lines)."""
    return [p.strip() for p in _BLANK.split(text or "") if p.strip()]


async def _note(conn: aiosqlite.Connection, idea_id: int) -> Note | None:
    async with conn.execute(
        "SELECT id, target_kind, target_id, title, revision, updated_at, deleted_at "
        "FROM notes WHERE owner_id = 1 AND target_kind = 'idea' AND target_id = ?",
        (str(idea_id),),
    ) as cur:
        r = await cur.fetchone()
    return Note(id=r[0], target_kind=r[1], target_id=r[2], title=r[3],
                revision=r[4], updated_at=r[5], deleted_at=r[6]) if r else None


async def idea_note_text_or_none(conn: aiosqlite.Connection, idea_id: int) -> str | None:
    """The idea note's text (block texts joined by blank lines), or ``None``
    when the idea has no note in the store yet (not migrated). A soft-deleted
    idea note reads as an empty note, never as the stale column text."""
    note = await _note(conn, idea_id)
    if note is None:
        return None
    if note.deleted_at is not None:
        return ""
    blocks = await list_blocks(conn, note.id)
    texts = [(b.text or block_text(b.type, b.props, b.content)).strip() for b in blocks]
    return "\n\n".join(t for t in texts if t)


async def idea_note_texts(
    conn: aiosqlite.Connection, idea_ids: list[int],
) -> dict[int, str]:
    """``idea_note_text_or_none`` for many ideas in ONE query; ideas with no
    note in the store are absent from the result."""
    if not idea_ids:
        return {}
    marks = ",".join("?" * len(idea_ids))
    async with conn.execute(
        "SELECT n.target_id, n.deleted_at, b.id, b.note_id, b.parent_id, b.type, "
        "b.payload_json, b.text, b.author, b.section_key, b.changed_rev, b.order_key "
        "FROM notes n LEFT JOIN note_blocks b "
        "ON b.note_id = n.id AND b.deleted_at IS NULL "
        f"WHERE n.owner_id = 1 AND n.target_kind = 'idea' AND n.target_id IN ({marks})",
        [str(i) for i in idea_ids],
    ) as cur:
        rows = await cur.fetchall()
    grouped: dict[int, list[Sequence[Any]]] = {}
    deleted: set[int] = set()
    for r in rows:
        iid = int(r[0])
        bucket = grouped.setdefault(iid, [])
        if r[1] is not None:
            deleted.add(iid)
        elif r[2] is not None:
            bucket.append(r[2:])
    out: dict[int, str] = {}
    for iid, brows in grouped.items():
        if iid in deleted:
            out[iid] = ""
            continue
        texts = [(b.text or block_text(b.type, b.props, b.content)).strip()
                 for b in order_block_rows(brows)]
        out[iid] = "\n\n".join(t for t in texts if t)
    return out


async def idea_note_text(conn: aiosqlite.Connection, idea_id: int) -> str:
    return await idea_note_text_or_none(conn, idea_id) or ""


async def set_idea_note_text(
    conn: aiosqlite.Connection, idea_id: int, text: str, *, actor: Actor,
    summary: str = "",
) -> None:
    """Make the idea note hold exactly ``text``'s paragraphs: one revision
    (every live block deleted, the paragraphs appended; none when nothing
    changes), atomic with the caller's transaction when it is inside one. A
    soft-deleted idea note is restored when its text changes."""
    async with conn.execute("SELECT title FROM ideas WHERE id = ?", (idea_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"idea {idea_id} not found")
    new = paragraphs(text)
    async with tx(conn):
        if "\n\n".join(new) == (await idea_note_text_or_none(conn, idea_id) or ""):
            return
        note = await get_or_create_note(
            conn, target_kind="idea", target_id=str(idea_id), title="")
        if note.deleted_at is not None:
            await conn.execute(
                "UPDATE notes SET deleted_at = NULL WHERE id = ?", (note.id,))
        live = await list_blocks(conn, note.id)
        ids = {b.id for b in live}
        # Roots only: deleting a block takes its subtree with it.
        ops: list[Op] = [Op(op="delete", block_id=b.id) for b in live
                         if b.parent_id is None or b.parent_id not in ids]
        ops += [Op(op="insert", block=BlockIn(
            type="paragraph", content=[{"type": "text", "text": p, "styles": {}}]))
            for p in new]
        await _apply_in_tx(conn, note.id, base_revision=note.revision, ops=ops,
                           actor=actor, run_id=None, summary=summary)
