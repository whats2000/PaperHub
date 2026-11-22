"""Marks with no place in their paper's note (2026-11-22: a chip taken out
of the note left mk_a1kgpabcjz on the paper, undeletable) are soft-deleted,
once; marks anchored by a block or a chip, and deleted ones, are left."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from paperhub.cli.repair_orphan_marks import repair
from paperhub.db.migrate import apply_schema
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note


def _chip(mark_id: str) -> dict[str, object]:
    return {"type": "markCite", "props": {"markId": mark_id, "text": "",
                                          "paperId": 7}}


async def _seed(db: Path) -> None:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, sha256, title, "
            "source_path, source_dir_path, html_path) VALUES "
            "(7, 'k7', 'pdf_upload', 'abc', 'P', 's', 'd', 'h')")
        pieces = json.dumps([{"quote": "q", "page": 0}])
        for mid, block, deleted in (
                ("mk_block", "q1", None),     # its reference block is there
                ("mk_chip", "b_c", None),     # cited inline in p1
                ("mk_cell", "b_t", None),     # cited inside a table cell
                ("mk_orphan", "b_o", None),   # neither: repaired
                ("mk_gone", "b_g", "then")):  # deleted already: left alone
            await conn.execute(
                "INSERT INTO note_marks (id, paper_content_id, block_id, kind, "
                "view, pieces_json, created_at, color, style, deleted_at) "
                "VALUES (?, 7, ?, 'text', 'pdf', ?, 'now', 'green', "
                "'highlight', ?)", (mid, block, pieces, deleted))
        await conn.commit()
        n = await get_or_create_note(conn, target_kind="paper", target_id="7",
                                     title="")
        cell = {"type": "tableCell", "props": {},
                "content": [_chip("mk_cell")]}
        await apply_ops(conn, n.id, base_revision=n.revision, actor="user", ops=[
            Op(op="insert", block_id="q1", block=BlockIn(
                type="paperQuote", props={
                    "markId": "mk_block", "color": "green", "kind": "text",
                    "style": "highlight", "quote": "q",
                    "pieces": [{"quote": "q", "page": 0}]})),
            Op(op="insert", block_id="p1", after="q1", block=BlockIn(
                type="paragraph", content=[_chip("mk_chip")])),
            Op(op="insert", block_id="t1", after="p1", block=BlockIn(
                type="table", content={"type": "tableContent", "rows": [
                    {"cells": [cell]}]})),
        ])


async def _deleted(db: Path) -> dict[str, bool]:
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT id, deleted_at FROM note_marks ORDER BY id") as cur:
        return {r[0]: r[1] is not None for r in await cur.fetchall()}


async def test_dry_run_names_the_orphans_and_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    before = await _deleted(db)
    out = await repair(db, dry_run=True)
    assert out["checked"] == 4 and out["anchored"] == 3
    assert [o["mark_id"] for o in out["orphans"]] == ["mk_orphan"]
    assert await _deleted(db) == before


async def test_soft_deletes_each_orphan_once(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    out = await repair(db, dry_run=False)
    assert [o["mark_id"] for o in out["orphans"]] == ["mk_orphan"]
    assert await _deleted(db) == {"mk_block": False, "mk_cell": False,
                                  "mk_chip": False, "mk_gone": True,
                                  "mk_orphan": True}
    again = await repair(db, dry_run=False)
    assert again["orphans"] == [] and again["checked"] == 3
