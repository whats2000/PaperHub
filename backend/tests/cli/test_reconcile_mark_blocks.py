"""Task 16: mark blocks whose colour / style disagree with their mark row
(agent recolours before the fix) are redrawn from the row, once."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from paperhub.cli.reconcile_mark_blocks import reconcile
from paperhub.db.migrate import apply_schema
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note


def _props(mark_id: str, color: str, style: str) -> dict[str, object]:
    return {"markId": mark_id, "color": color, "kind": "text", "style": style,
            "quote": "q", "pieces": [{"quote": "q", "page": 0}]}


async def _seed(db: Path) -> int:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, sha256, title, "
            "source_path, source_dir_path, html_path) VALUES "
            "(7, 'k7', 'pdf_upload', 'abc', 'P', 's', 'd', 'h')")
        await conn.execute(
            "INSERT INTO note_marks (id, paper_content_id, block_id, kind, view, "
            "pieces_json, created_at, color, style) VALUES "
            "('mk_1', 7, 'q1', 'text', 'pdf', ?, 'now', 'blue', 'wavy')",
            (json.dumps([{"quote": "q", "page": 0}]),))
        await conn.commit()
        n = await get_or_create_note(conn, target_kind="session", target_id="1",
                                     title="")
        await apply_ops(conn, n.id, base_revision=n.revision, actor="user", ops=[
            # Behind its row (an agent recolour before the fix).
            Op(op="insert", block_id="q1", block=BlockIn(
                type="paperQuote", props=_props("mk_1", "green", "highlight"))),
            Op(op="insert", block_id="q2", after="q1", block=BlockIn(
                type="paperQuote", props=_props("mk_1", "blue", "wavy"))),
            # No mark row: left alone.
            Op(op="insert", block_id="q3", after="q2", block=BlockIn(
                type="paperQuote", props=_props("mk_gone", "pink", "bar"))),
        ])
        return n.id


async def _props_of(db: Path) -> dict[str, dict[str, object]]:
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT id, payload_json FROM note_blocks ORDER BY id") as cur:
        return {r[0]: json.loads(r[1])["props"] for r in await cur.fetchall()}


async def _change_sets(db: Path) -> list[tuple[str, str]]:
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT actor, ops_json FROM note_change_sets ORDER BY created_at"
    ) as cur:
        return [(r[0], r[1]) for r in await cur.fetchall()]


async def test_dry_run_counts_and_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    before = await _props_of(db)
    counts = await reconcile(db, dry_run=True)
    assert counts == {"checked": 2, "matching": 1, "redrawn": 1, "no_mark": 1}
    assert await _props_of(db) == before


async def test_redraws_from_the_row_as_a_system_change_once(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    sets = len(await _change_sets(db))
    counts = await reconcile(db, dry_run=False)
    assert counts == {"checked": 2, "matching": 1, "redrawn": 1, "no_mark": 1}
    props = await _props_of(db)
    assert (props["q1"]["color"], props["q1"]["style"]) == ("blue", "wavy")
    assert props["q3"]["color"] == "pink"
    changes = await _change_sets(db)
    assert len(changes) == sets + 1 and changes[-1][0] == "system"
    # The row is the source: it is not touched.
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT color, style FROM note_marks WHERE id = 'mk_1'") as cur:
        assert await cur.fetchone() == ("blue", "wavy")
    # Idempotent: nothing left to redraw, no new change set.
    again = await reconcile(db, dry_run=False)
    assert again == {"checked": 2, "matching": 2, "redrawn": 0, "no_mark": 1}
    assert len(await _change_sets(db)) == sets + 1
