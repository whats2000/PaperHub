"""H5b.1: the idea note lives in the notes store; the agent and the API read it there."""
from __future__ import annotations

from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

from paperhub.ideation.agent import _rejected
from paperhub.notes.ideas import idea_note_text, set_idea_note_text


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
