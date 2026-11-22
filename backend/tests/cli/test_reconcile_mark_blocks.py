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
    assert counts == {"checked": 2, "matching": 1, "redrawn": 1, "no_mark": 1,
                      "rejected": []}
    assert await _props_of(db) == before


async def test_redraws_from_the_row_as_a_system_change_once(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    sets = len(await _change_sets(db))
    counts = await reconcile(db, dry_run=False)
    assert counts == {"checked": 2, "matching": 1, "redrawn": 1, "no_mark": 1,
                      "rejected": []}
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
    assert again == {"checked": 2, "matching": 2, "redrawn": 0, "no_mark": 1,
                     "rejected": []}
    assert len(await _change_sets(db)) == sets + 1


async def test_a_rejected_redraw_is_counted_and_named(tmp_path: Path,
                                                    monkeypatch) -> None:
    from paperhub.cli import reconcile_mark_blocks as cli
    from paperhub.notes.ops import ApplyResult

    async def refuse(conn, note_id, **kw):  # the block changed meanwhile
        return ApplyResult(
            note_id=note_id, revision=kw["base_revision"], change_set_id=None,
            applied=[], suggested=[],
            rejected=[{"block_id": "q1", "reason": "changed_since_base",
                       "current": None}])
    monkeypatch.setattr(cli, "apply_ops", refuse)
    db = tmp_path / "p.db"
    n = await _seed(db)
    counts = await reconcile(db, dry_run=False)
    assert counts["redrawn"] == 0
    assert counts["rejected"] == [{"note_id": n, "block_id": "q1",
                                   "reason": "changed_since_base"}]


async def test_the_history_keeps_the_colour_shown_before(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    await _seed(db)
    await reconcile(db, dry_run=False)
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT before_json, after_json FROM note_block_history "
            "WHERE block_id = 'q1' ORDER BY id DESC LIMIT 1") as cur:
        before, after = (json.loads(x) for x in await cur.fetchone())
    assert (before["props"]["color"], before["props"]["style"]) == ("green", "highlight")
    assert (after["props"]["color"], after["props"]["style"]) == ("blue", "wavy")


def test_main_exits_1_when_a_redraw_was_refused(tmp_path: Path, monkeypatch,
                                                capsys) -> None:
    import asyncio
    from types import SimpleNamespace

    from paperhub.cli import reconcile_mark_blocks as cli
    from paperhub.notes.ops import ApplyResult

    db = tmp_path / "p.db"
    asyncio.run(_seed(db))
    monkeypatch.setattr(cli, "load_settings", lambda: SimpleNamespace(db_path=db))
    assert cli.main(["--dry-run"]) == 0

    async def refuse(conn, note_id, **kw):
        return ApplyResult(
            note_id=note_id, revision=kw["base_revision"], change_set_id=None,
            applied=[], suggested=[],
            rejected=[{"block_id": "q1", "reason": "changed_since_base",
                       "current": None}])
    monkeypatch.setattr(cli, "apply_ops", refuse)
    assert cli.main([]) == 1
    assert "rejected: note" in capsys.readouterr().out


async def test_undo_of_the_reconcile_set_is_refused(tmp_path: Path) -> None:
    from paperhub.notes.ops import undo_change_set
    db = tmp_path / "p.db"
    await _seed(db)
    await reconcile(db, dry_run=False)
    async with aiosqlite.connect(db) as conn:
        async with conn.execute(
                "SELECT id FROM note_change_sets WHERE actor = 'system' "
                "ORDER BY created_at DESC LIMIT 1") as cur:
            [cs] = await cur.fetchone()
        out = await undo_change_set(conn, cs)
        assert [r["reason"] for r in out.rejected] == ["follows_mark"]
        async with conn.execute(
                "SELECT color, style FROM note_marks WHERE id = 'mk_1'") as cur:
            assert await cur.fetchone() == ("blue", "wavy")
        async with conn.execute(
                "SELECT status FROM note_change_sets WHERE id = ?", (cs,)) as cur:
            assert (await cur.fetchone())[0] == "applied"
    props = await _props_of(db)
    assert (props["q1"]["color"], props["q1"]["style"]) == ("blue", "wavy")
