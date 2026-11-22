"""Plan H5b.1: every live table without lines gets the Paper preset's lines
for its grid (what it showed by default), once."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.cli.backfill_table_borders import backfill
from paperhub.db.migrate import apply_schema
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note
from paperhub.notes.table_borders import dump_borders, parse_borders, preset_lines


def _c(text: str, **props: Any) -> dict[str, Any]:
    return {"type": "tableCell", "props": props,
            "content": [{"type": "text", "text": text, "styles": {}}]}


PLAIN = {"type": "tableContent", "columnWidths": [None, None], "headerRows": 1,
         "rows": [{"cells": ["a", "b"]}, {"cells": ["1", "2"]}]}
MERGED = {"type": "tableContent", "columnWidths": [None, None, None],
          "headerRows": 2, "rows": [
              {"cells": [_c("M", rowspan=2), _c("S", colspan=2)]},
              {"cells": [_c("r"), _c("t")]},
              {"cells": [_c("x"), _c("1"), _c("2")]}]}
NOTION = dump_borders(preset_lines(2, 2, 1, "notion"))


async def _seed(db: Path) -> tuple[int, int]:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        n = await get_or_create_note(conn, target_kind="session", target_id="1",
                                     title="")
        await apply_ops(conn, n.id, base_revision=n.revision, actor="user", ops=[
            Op(op="insert", block_id="t1", block=BlockIn(type="table", content=PLAIN)),
            Op(op="insert", block_id="t2", after="t1",
               block=BlockIn(type="table", content=MERGED)),
            Op(op="insert", block_id="t3", after="t2", block=BlockIn(
                type="table", content=PLAIN, props={"borders": NOTION})),
            Op(op="insert", block_id="p1", after="t3",
               block=BlockIn(type="paragraph", content="text")),
        ])
        trashed = await get_or_create_note(conn, target_kind="session",
                                           target_id="2", title="")
        await apply_ops(conn, trashed.id, base_revision=trashed.revision,
                        actor="user", ops=[
                            Op(op="insert", block_id="t9",
                               block=BlockIn(type="table", content=PLAIN))])
        await conn.execute("UPDATE notes SET deleted_at = 'now' WHERE id = ?",
                           (trashed.id,))
        await conn.commit()
        return n.id, trashed.id


async def _props_of(db: Path) -> dict[str, dict[str, Any]]:
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT id, payload_json FROM note_blocks ORDER BY id") as cur:
        return {r[0]: json.loads(r[1]).get("props") or {}
                for r in await cur.fetchall()}


async def _change_sets(db: Path) -> list[tuple[int, str]]:
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT note_id, actor FROM note_change_sets ORDER BY created_at"
    ) as cur:
        return [(r[0], r[1]) for r in await cur.fetchall()]


async def test_dry_run_counts_and_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    before = await _props_of(db)
    counts = await backfill(db, dry_run=True)
    assert counts == {"checked": 3, "set": 2, "already": 1, "rejected": []}
    assert await _props_of(db) == before


async def test_sets_paper_lines_once_as_a_system_change(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    nid, trashed = await _seed(db)
    sets = await _change_sets(db)
    counts = await backfill(db, dry_run=False)
    assert counts == {"checked": 3, "set": 2, "already": 1, "rejected": []}
    props = await _props_of(db)
    assert parse_borders(props["t1"]["borders"]) == preset_lines(2, 2, 1, "paper")
    merged = parse_borders(props["t2"]["borders"])
    assert merged == preset_lines(3, 3, 2, "paper") and merged.h[2] == "111"
    assert props["t3"]["borders"] == NOTION
    assert "borders" not in props["t9"]
    new = (await _change_sets(db))[len(sets):]
    assert new == [(nid, "system")]
    assert trashed not in {n for n, _ in new}
    again = await backfill(db, dry_run=False)
    assert again == {"checked": 3, "set": 0, "already": 3, "rejected": []}
    assert len(await _change_sets(db)) == len(sets) + 1


async def test_an_invalid_table_is_rejected_and_the_run_goes_on(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "UPDATE note_blocks SET payload_json = ? WHERE id = 't1'",
            (json.dumps({"props": {}, "content": {"type": "tableContent",
                                                  "rows": "bad"}}),))
        await conn.commit()
    counts = await backfill(db, dry_run=False)
    assert counts["set"] == 1 and counts["already"] == 1
    [rej] = counts["rejected"]
    assert rej["block_id"] == "t1" and rej["reason"]
    assert "borders" in (await _props_of(db))["t2"]
