"""Comments on note text (H5b.1 Task 16): threads, replies, status, and the
guarantee that thread text is never note text."""
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        r = await c.get("/notes/target", params={"kind": "session", "id": 1})
        nid = int(r.json()["note"]["id"])
        res = await c.post(f"/notes/{nid}/ops", json={"base_revision": 0, "ops": [
            {"op": "insert", "block": {"type": "paragraph", "content": [
                {"type": "text", "text": "hello world"}]}}]})
        yield c, nid, res.json()["applied"][0], tmp_path


async def _new(c: AsyncClient, nid: int, bid: str, text: str = "why?") -> dict:
    r = await c.post(f"/notes/{nid}/comments",
                     json={"block_id": bid, "quote": "hello", "text": text})
    assert r.status_code == 200, r.text
    return r.json()


async def test_create_reply_resolve_reopen_delete(env) -> None:
    c, nid, bid, _ = env
    t = await _new(c, nid, bid)
    assert t["status"] == "open" and t["block_id"] == bid and t["quote"] == "hello"
    assert [i["text"] for i in t["items"]] == ["why?"]
    r = await c.post(f"/notes/comments/{t['id']}/items", json={"text": "because"})
    assert r.status_code == 200
    assert [i["text"] for i in r.json()["items"]] == ["why?", "because"]
    r = await c.patch(f"/notes/comments/{t['id']}", json={"status": "resolved"})
    assert r.json()["status"] == "resolved"
    r = await c.patch(f"/notes/comments/{t['id']}", json={"status": "open"})
    assert r.json()["status"] == "open"
    r = await c.patch(f"/notes/comments/{t['id']}", json={"status": "deleted"})
    assert r.json()["status"] == "deleted"
    r = await c.post(f"/notes/comments/{t['id']}/items", json={"text": "late"})
    assert r.status_code == 409
    r = await c.patch(f"/notes/comments/{t['id']}", json={"status": "resolved"})
    assert r.status_code == 409
    # reopening is the Undo of Delete
    r = await c.patch(f"/notes/comments/{t['id']}", json={"status": "open"})
    assert r.status_code == 200 and r.json()["status"] == "open"
    assert [i["text"] for i in r.json()["items"]] == ["why?", "because"]


async def test_detail_lists_threads_but_not_deleted(env) -> None:
    c, nid, bid, _ = env
    keep = await _new(c, nid, bid)
    gone = await _new(c, nid, bid, "bye")
    await c.patch(f"/notes/comments/{gone['id']}", json={"status": "deleted"})
    d = (await c.get(f"/notes/{nid}")).json()
    assert [x["id"] for x in d["comments"]] == [keep["id"]]
    assert d["comments"][0]["items"][0]["text"] == "why?"


async def test_validation(env) -> None:
    c, nid, bid, _ = env
    body = {"block_id": bid, "quote": "q", "text": "x"}
    assert (await c.post("/notes/999/comments", json=body)).status_code == 404
    assert (await c.post(f"/notes/{nid}/comments",
                         json={**body, "block_id": "nope"})).status_code == 422
    assert (await c.post(f"/notes/{nid}/comments",
                         json={**body, "text": "   "})).status_code == 422
    t = await _new(c, nid, bid)
    assert (await c.post(f"/notes/comments/{t['id']}/items",
                         json={"text": ""})).status_code == 422
    assert (await c.post("/notes/comments/zzz/items",
                         json={"text": "x"})).status_code == 404
    assert (await c.patch("/notes/comments/zzz",
                          json={"status": "resolved"})).status_code == 404
    assert (await c.patch(f"/notes/comments/{t['id']}",
                          json={"status": "bogus"})).status_code == 422


async def test_comment_text_is_not_note_text(env) -> None:
    c, nid, bid, tmp = env
    t = await _new(c, nid, bid, "secretcomment")
    # the editor puts the thread id on the words as an inline style
    r = await c.post(f"/notes/{nid}/ops", json={"base_revision": 1, "ops": [{
        "op": "update", "block_id": bid, "block": {"type": "paragraph", "content": [
            {"type": "text", "text": "hello", "styles": {"comment": t["id"]}},
            {"type": "text", "text": " world", "styles": {}}]}}]})
    assert r.status_code == 200 and r.json()["applied"] == [bid], r.text
    got = (await c.get(f"/notes/{nid}")).json()
    block = next(b for b in got["blocks"] if b["id"] == bid)
    assert block["text"] == "hello world"
    assert block["content"][0]["styles"] == {"comment": t["id"]}
    async with aiosqlite.connect(tmp / "paperhub.db") as conn:
        async with conn.execute(
            "SELECT count(*) FROM note_blocks_fts WHERE note_blocks_fts MATCH "
            "'secretcomment'") as cur:
            assert (await cur.fetchone())[0] == 0
        async with conn.execute(
            "SELECT count(*) FROM note_blocks_fts WHERE note_blocks_fts MATCH "
            "'hello'") as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute(
            "SELECT text FROM note_blocks WHERE note_id = ?", (nid,)) as cur:
            texts = [r[0] for r in await cur.fetchall()]
    assert texts == ["hello world"]
    assert all(t["id"] not in x for x in texts)


async def test_deleting_the_note_deletes_its_threads(env) -> None:
    c, nid, bid, tmp = env
    await _new(c, nid, bid)
    async with aiosqlite.connect(tmp / "paperhub.db") as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        for t in ("note_comments", "note_comment_items"):
            async with conn.execute(f"SELECT owner_id FROM {t}") as cur:
                assert (await cur.fetchone())[0] == 1
        await conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
        await conn.commit()
        for t in ("note_comments", "note_comment_items"):
            async with conn.execute(f"SELECT count(*) FROM {t}") as cur:
                assert (await cur.fetchone())[0] == 0


async def test_blank_quote_is_rejected(env) -> None:
    c, nid, bid, _ = env
    for q in ("", "   "):
        r = await c.post(f"/notes/{nid}/comments",
                         json={"block_id": bid, "quote": q, "text": "x"})
        assert r.status_code == 422
