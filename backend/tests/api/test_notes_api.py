from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes.ops import BlockIn, Op, apply_ops

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat')")
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', 's', 'd', 'h')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


_INSERT = {"op": "insert", "block": {
    "type": "paragraph", "content": [{"type": "text", "text": "hello"}]}}


async def _session_note(client: AsyncClient) -> int:
    r = await client.get("/notes/target", params={"kind": "session", "id": 1})
    return int(r.json()["note"]["id"])


async def test_target_endpoint_creates_once(client: AsyncClient) -> None:
    a = (await client.get("/notes/target", params={"kind": "session", "id": 1})).json()
    b = (await client.get("/notes/target", params={"kind": "session", "id": 1})).json()
    assert a["note"]["id"] == b["note"]["id"]
    assert a["note"]["title"] == "Chat" and a["blocks"] == []
    p = (await client.get("/notes/target", params={"kind": "paper", "id": 1})).json()
    assert p["note"]["title"] == "A Paper" and p["note"]["id"] != a["note"]["id"]
    assert (await client.get(
        "/notes/target", params={"kind": "session", "id": 99})).status_code == 404
    assert (await client.get(
        "/notes/target", params={"kind": "paper", "id": 99})).status_code == 404


async def test_ops_endpoint_returns_apply_result(client: AsyncClient) -> None:
    nid = await _session_note(client)
    r = await client.post(f"/notes/{nid}/ops",
                          json={"base_revision": 0, "ops": [_INSERT]})
    assert r.status_code == 200
    res = r.json()
    assert res["revision"] == 1 and len(res["applied"]) == 1 and res["rejected"] == []
    bid = res["applied"][0]

    detail = (await client.get(f"/notes/{nid}")).json()
    assert [b["id"] for b in detail["blocks"]] == [bid]
    assert detail["blocks"][0]["author"] == "user"
    assert detail["suggestions"] == [] and detail["comments"] == []

    # A stale base is a rejection in the result, never a 409.
    upd = {"op": "update", "block_id": bid, "block": {
        "type": "paragraph", "content": [{"type": "text", "text": "x"}]}}
    await client.post(f"/notes/{nid}/ops", json={"base_revision": 1, "ops": [upd]})
    stale = await client.post(f"/notes/{nid}/ops",
                              json={"base_revision": 0, "ops": [upd]})
    assert stale.status_code == 200
    assert stale.json()["rejected"][0]["reason"] == "changed_since_base"
    future = await client.post(f"/notes/{nid}/ops",
                               json={"base_revision": 99, "ops": [upd]})
    assert future.status_code == 422
    assert (await client.post("/notes/999/ops", json={
        "base_revision": 0, "ops": []})).status_code == 404


async def test_delete_restore_undo_reapply(client: AsyncClient) -> None:
    nid = await _session_note(client)
    bid = (await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 0, "ops": [_INSERT]})).json()["applied"][0]
    dele = (await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 1, "ops": [{"op": "delete", "block_id": bid}]})).json()
    assert (await client.get(f"/notes/{nid}")).json()["blocks"] == []
    back = (await client.post(f"/notes/{nid}/restore",
                              json={"block_ids": [bid]})).json()
    assert back["applied"] == [bid]

    cs = dele["change_set_id"]
    undo = await client.post(f"/notes/change-sets/{cs}/undo")
    assert undo.status_code == 200 and undo.json()["change_set_id"] == cs
    again = await client.post(f"/notes/change-sets/{cs}/reapply")
    assert again.status_code == 200 and again.json()["change_set_id"] != cs
    assert (await client.post(f"/notes/change-sets/{cs}/reapply")).status_code == 409
    assert (await client.post("/notes/change-sets/nope/undo")).status_code == 404


async def test_suggestion_endpoint(client: AsyncClient, tmp_path: Path) -> None:
    nid = await _session_note(client)
    bid = (await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 0, "ops": [_INSERT]})).json()["applied"][0]
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        res = await apply_ops(conn, nid, base_revision=1, actor="agent", ops=[
            Op(op="update", block_id=bid, block=BlockIn(
                type="paragraph", content=[{"type": "text", "text": "new"}]))])
    sid = res.suggested[0]["suggestion_id"]
    detail = (await client.get(f"/notes/{nid}")).json()
    assert [s["id"] for s in detail["suggestions"]] == [sid]
    r = await client.post(f"/notes/suggestions/{sid}", json={"accept": True})
    assert r.status_code == 200 and r.json()["applied"] == [bid]
    assert (await client.post(f"/notes/suggestions/{sid}",
                              json={"accept": True})).status_code == 409
    assert (await client.post("/notes/suggestions/zzz",
                              json={"accept": True})).status_code == 404


async def test_change_set_status_endpoint(client: AsyncClient) -> None:
    nid = await _session_note(client)
    cs = (await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 0, "ops": [_INSERT]})).json()["change_set_id"]
    got = (await client.get(f"/notes/change-sets/{cs}")).json()
    assert got == {"id": cs, "note_id": nid, "status": "applied",
                   "reapplied_by": None}
    await client.post(f"/notes/change-sets/{cs}/undo")
    assert (await client.get(f"/notes/change-sets/{cs}")).json()["status"] == "undone"
    again = (await client.post(f"/notes/change-sets/{cs}/reapply")).json()
    got = (await client.get(f"/notes/change-sets/{cs}")).json()
    assert got["reapplied_by"] == again["change_set_id"]
    assert (await client.get("/notes/change-sets/nope")).status_code == 404
