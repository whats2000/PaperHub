"""H5b.1: the idea note lives in the notes store; the agent and the API read it there."""
from __future__ import annotations

from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

from paperhub.ideation.agent import _rejected
from paperhub.notes.ideas import idea_note_text, set_idea_note_text
from paperhub.notes.ops import BlockIn, Op, apply_ops


async def _doc_and_idea(conn: aiosqlite.Connection, *, status: str, note: str = "") -> int:
    await conn.execute(
        "INSERT OR IGNORE INTO paper_content (id, content_key, kind, sha256, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES (1, 'pdf:1', 'pdf_upload', ?, 'D', '', '', '')", ("1" * 64,))
    cur = await conn.execute(
        "INSERT INTO ideas (source_doc, source_section, title, status, note) "
        "VALUES (1, 'S', 'an idea', ?, ?)", (status, note))
    await conn.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def test_rejected_prompt_uses_idea_note(migrated_db: aiosqlite.Connection) -> None:
    iid = await _doc_and_idea(migrated_db, status="failed", note="stale column text")
    await set_idea_note_text(migrated_db, iid, "too slow in practice\n\nand costly",
                             actor="user")
    rejected = await _rejected(migrated_db, None)
    assert len(rejected) == 1
    assert "too slow in practice" in rejected[0].note
    assert "stale column text" not in rejected[0].note


async def test_patch_note_writes_idea_note(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    iid = await _doc_and_idea(conn, status="new")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.patch(f"/ideation/ideas/{iid}", json={"note": "one\n\ntwo"})
        assert r.status_code == 200 and r.json()["note"] == "one\n\ntwo"
        r = await c.patch(f"/ideation/ideas/{iid}", json={"note": "replaced"})
        assert r.json()["note"] == "replaced"
    assert await idea_note_text(conn, iid) == "replaced"
    async with conn.execute(
        "SELECT actor FROM note_revisions ORDER BY revision DESC LIMIT 1") as cur:
        assert (await cur.fetchone()) == ("user",)
    async with conn.execute("SELECT note FROM ideas WHERE id = ?", (iid,)) as cur:
        assert (await cur.fetchone()) == ("",)  # the column stays untouched


async def _revisions(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT COUNT(*) FROM note_revisions") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_patch_with_heading_and_list_present(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    iid = await _doc_and_idea(conn, status="new")
    await set_idea_note_text(conn, iid, "seed", actor="system")
    async with conn.execute("SELECT id, revision FROM notes WHERE target_id = ?",
                            (str(iid),)) as cur:
        row = await cur.fetchone()
    assert row is not None
    nid, rev = int(row[0]), int(row[1])
    await apply_ops(conn, nid, base_revision=rev, actor="user", ops=[
        Op(op="insert", block=BlockIn(type="heading", props={"level": 2},
                                      content="Plan")),
        Op(op="insert", block=BlockIn(type="bulletListItem", content="step one")),
    ])
    before = await idea_note_text(conn, iid)
    assert before == "seed\n\nPlan\n\nstep one"
    revs = await _revisions(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.patch(f"/ideation/ideas/{iid}", json={"note": before})
        assert r.status_code == 200
        assert await _revisions(conn) == revs  # unchanged: nothing written
        r = await c.patch(f"/ideation/ideas/{iid}", json={"note": "new a\n\nnew b"})
        assert r.json()["note"] == "new a\n\nnew b"
    async with conn.execute(
        "SELECT type, text FROM note_blocks WHERE note_id = ? AND deleted_at IS NULL "
        "ORDER BY order_key", (nid,)) as cur:
        assert [tuple(x) for x in await cur.fetchall()] == [
            ("paragraph", "new a"), ("paragraph", "new b")]


async def test_patch_clears_note_and_restores_deleted(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    iid = await _doc_and_idea(conn, status="new", note="column text")
    await set_idea_note_text(conn, iid, "stored", actor="system")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.patch(f"/ideation/ideas/{iid}", json={"note": ""})
        assert r.json()["note"] == ""
        await conn.execute("UPDATE notes SET deleted_at = datetime('now')")
        await conn.commit()
        r = await c.patch(f"/ideation/ideas/{iid}", json={"note": "back"})
        assert r.json()["note"] == "back"
    async with conn.execute("SELECT COUNT(*), MAX(deleted_at) FROM notes") as cur:
        assert (await cur.fetchone()) == (1, None)
    async with conn.execute("SELECT note FROM ideas WHERE id = ?", (iid,)) as cur:
        assert (await cur.fetchone()) == ("column text",)


async def test_deleted_idea_note_reads_empty_not_column(
    migrated_db: aiosqlite.Connection,
) -> None:
    iid = await _doc_and_idea(migrated_db, status="failed", note="stale column")
    await set_idea_note_text(migrated_db, iid, "kept", actor="system")
    await migrated_db.execute("UPDATE notes SET deleted_at = datetime('now')")
    await migrated_db.commit()
    assert await idea_note_text(migrated_db, iid) == ""
    assert "stale column" not in (await _rejected(migrated_db, None))[0].note


async def test_unmigrated_idea_falls_back_to_column(app_with_db: tuple[Any, Any]) -> None:
    from paperhub.api.ideation import _ideas

    _app, conn = app_with_db
    iid = await _doc_and_idea(conn, status="failed", note="column only")
    assert (await _rejected(conn, None))[0].note == "column only"
    assert (await _ideas(conn, "id = ?", (iid,)))[0].note == "column only"
