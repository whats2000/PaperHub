"""Paper notes: the per-paper note, its section headings and parts."""
from __future__ import annotations

import aiosqlite

from paperhub.agents.sl_seed import section_names_from_json
from paperhub.db.connection import in_write_transaction, write_lock
from paperhub.notes.models import Block, Note
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_note, get_or_create_note, list_blocks


async def paper_note(conn: aiosqlite.Connection, paper_content_id: int) -> Note:
    async with conn.execute(
        "SELECT title FROM paper_content WHERE id = ?", (paper_content_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"paper_content {paper_content_id} not found")
    return await get_or_create_note(
        conn, target_kind="paper", target_id=str(paper_content_id),
        title=str(row[0]))


async def _section_order(conn: aiosqlite.Connection,
                         paper_content_id: int) -> list[str]:
    async with conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    return section_names_from_json(row[0] if row else None)


async def ensure_section(
    conn: aiosqlite.Connection, note_id: int, paper_content_id: int,
    section_key: str,
) -> str:
    """The ``section`` block for ``section_key``, inserted in paper order."""
    blocks = await list_blocks(conn, note_id)
    sections = [b for b in blocks if b.type == "section"]
    for b in sections:
        if b.section_key == section_key:
            return b.id
    order = await _section_order(conn, paper_content_id)
    rank = order.index(section_key) if section_key in order else None
    before: str | None = None
    if rank is not None:
        for b in sections:
            r = order.index(b.section_key) if b.section_key in order else None
            if r is not None and r > rank:
                before = b.id
                break
    note = await get_note(conn, note_id)
    assert note is not None
    op = Op(op="insert", block=BlockIn(type="section",
                                       props={"title": section_key}),
            before=before)
    res = await apply_ops(conn, note_id, base_revision=note.revision,
                          ops=[op], actor="user",
                          summary=f"Section {section_key}")
    if not res.applied:
        raise RuntimeError(f"could not create section {section_key!r}")
    block_id = res.applied[0]
    async with write_lock(conn):
        await conn.execute(
            "UPDATE note_blocks SET section_key = ? WHERE id = ?",
            (section_key, block_id))
        if not in_write_transaction():
            await conn.commit()
    return block_id


def _section_slice(blocks: list[Block], section_block_id: str) -> list[Block]:
    """Blocks after the section block, up to the next section."""
    idx = next((i for i, b in enumerate(blocks) if b.id == section_block_id),
               None)
    if idx is None:
        return []
    out: list[Block] = []
    for b in blocks[idx + 1:]:
        if b.type == "section":
            break
        out.append(b)
    return out


def _split(blocks: list[Block], section_block_id: str,
           ) -> list[tuple[list[Block], str]]:
    """Parts as ``(blocks, lead_id)``; lead is the block a part follows."""
    parts: list[tuple[list[Block], str]] = [([], section_block_id)]
    for b in _section_slice(blocks, section_block_id):
        if b.type == "divider":
            parts.append(([], b.id))
        else:
            parts[-1][0].append(b)
    return parts


def parts_of_section(blocks: list[Block],
                     section_block_id: str) -> list[list[Block]]:
    """A section's parts: only ``divider`` blocks split; the heading is in
    the first part and a sub-heading stays inside its part."""
    parts = [p for p, _ in _split(blocks, section_block_id)]
    if parts:
        sec = next((b for b in blocks if b.id == section_block_id), None)
        if sec is not None:
            parts[0] = [sec, *parts[0]]
    return parts


async def destination(
    conn: aiosqlite.Connection, note_id: int, section_block_id: str,
    hint_block_id: str | None,
) -> tuple[str | None, int, int]:
    """``(after_block_id, part_index, part_count)`` for a new block."""
    blocks = await list_blocks(conn, note_id)
    parts = _split(blocks, section_block_id)
    idx = len(parts) - 1
    if hint_block_id is not None:
        for i, (members, _) in enumerate(parts):
            if any(b.id == hint_block_id for b in members):
                idx = i
                break
    members, lead = parts[idx]
    return (members[-1].id if members else lead), idx, len(parts)
