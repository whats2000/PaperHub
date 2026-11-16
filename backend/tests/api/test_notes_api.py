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


async def test_target_endpoint_carries_open_suggestions_and_comments(
        client: AsyncClient, tmp_path: Path) -> None:
    """Opening a note shows its open suggestions and comment threads at once."""
    nid = await _session_note(client)
    bid = (await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 0, "ops": [_INSERT]})).json()["applied"][0]
    await client.post(f"/notes/{nid}/comments",
                      json={"block_id": bid, "quote": "hello", "text": "why?"})
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        res = await apply_ops(conn, nid, base_revision=1, actor="agent", ops=[
            Op(op="update", block_id=bid, block=BlockIn(
                type="paragraph", content=[{"type": "text", "text": "new"}]))])
    got = (await client.get("/notes/target",
                            params={"kind": "session", "id": 1})).json()
    assert [s["id"] for s in got["suggestions"]] == [
        res.suggested[0]["suggestion_id"]]
    assert [c["quote"] for c in got["comments"]] == ["hello"]


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
    assert {k: got[k] for k in ("id", "note_id", "status", "reapplied_by")} == {
        "id": cs, "note_id": nid, "status": "applied", "reapplied_by": None}
    assert got["head"] == {"id": cs, "status": "applied"}
    await client.post(f"/notes/change-sets/{cs}/undo")
    got = (await client.get(f"/notes/change-sets/{cs}")).json()
    assert got["status"] == "undone" and got["head"] == {"id": cs, "status": "undone"}
    again = (await client.post(f"/notes/change-sets/{cs}/reapply")).json()
    got = (await client.get(f"/notes/change-sets/{cs}")).json()
    assert got["reapplied_by"] == again["change_set_id"]
    # The card follows Apply again to the set that is live now.
    assert got["head"] == {"id": again["change_set_id"], "status": "applied"}
    await client.post(f"/notes/change-sets/{again['change_set_id']}/undo")
    got = (await client.get(f"/notes/change-sets/{cs}")).json()
    assert got["head"] == {"id": again["change_set_id"], "status": "undone"}
    assert (await client.get("/notes/change-sets/nope")).status_code == 404


async def test_change_set_detail_lists_the_changes(
        client: AsyncClient, tmp_path: Path) -> None:
    """What the chat card's Details shows: blocks added (no sections or
    dividers), and each suggestion with the user's text and the proposal."""
    nid = await _session_note(client)
    mine = (await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 0, "ops": [_INSERT]})).json()["applied"][0]
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        res = await apply_ops(conn, nid, base_revision=1, actor="agent", ops=[
            Op(op="insert", block=BlockIn(type="divider", props={})),
            Op(op="insert", block=BlockIn(
                type="paragraph", content=[{"type": "text", "text": "added"}])),
            Op(op="update", block_id=mine, block=BlockIn(
                type="paragraph", content=[{"type": "text", "text": "hello there"}]))])
    cs = res.change_set_id
    got = (await client.get(f"/notes/change-sets/{cs}")).json()
    assert got["note"] == {"target_kind": "session", "target_id": "1",
                           "title": "Chat"}
    added = [c for c in got["changes"] if c["kind"] == "added"]
    assert [c["text"] for c in added] == ["added"]
    sug = [c for c in got["changes"] if c["kind"] == "suggested"]
    assert sug == [{"kind": "suggested", "block_id": mine, "op": "update",
                    "before": "hello", "text": "hello there",
                    "status": "open"}]
    # After Undo and Apply again the suggestion lives under the new set and
    # is still listed once.
    await client.post(f"/notes/change-sets/{cs}/undo")
    await client.post(f"/notes/change-sets/{cs}/reapply")
    got = (await client.get(f"/notes/change-sets/{cs}")).json()
    sug = [c for c in got["changes"] if c["kind"] == "suggested"]
    assert [(c["block_id"], c["status"]) for c in sug] == [(mine, "open")]


async def test_ops_endpoint_replays_a_repeated_burst_key(client: AsyncClient) -> None:
    nid = await _session_note(client)
    body = {"base_revision": 0, "idempotency_key": "burst-1",
            "ops": [{**_INSERT, "block_id": "b1"}]}
    first = (await client.post(f"/notes/{nid}/ops", json=body)).json()
    again = (await client.post(f"/notes/{nid}/ops", json=body)).json()
    assert again == first and first["applied"] == ["b1"]
    detail = (await client.get(f"/notes/{nid}")).json()
    assert [b["id"] for b in detail["blocks"]] == ["b1"]
    assert detail["note"]["revision"] == 1


async def test_shared_by_counts_the_live_chats_the_note_is_linked_to(
    client: AsyncClient, tmp_path: Path,
) -> None:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.executescript(
            "INSERT INTO chat_sessions (id, title) VALUES (2, 'Two');"
            "INSERT INTO chat_sessions (id, title, deleted_at) VALUES (3, 'Gone', '2026-11-16');"
            "INSERT INTO chat_sessions (id, title, internal) VALUES (4, 'Hidden', 1);"
            "INSERT INTO papers (session_id, paper_content_id) VALUES "
            "(1, 1), (2, 1), (3, 1), (4, 1);")
        await conn.commit()
    # A paper's note: every live, listed chat that attaches the paper.
    paper = (await client.get("/notes/target", params={"kind": "paper", "id": 1})).json()
    assert paper["shared_by"] == 2
    detail = (await client.get(f"/notes/{paper['note']['id']}")).json()
    assert detail["shared_by"] == 2
    # A chat's note: that chat only.
    chat = (await client.get("/notes/target", params={"kind": "session", "id": 1})).json()
    assert chat["shared_by"] == 1
    assert (await client.get(f"/notes/{chat['note']['id']}")).json()["shared_by"] == 1


async def test_shared_by_for_an_idea_note_and_a_paper_in_no_chat(
    client: AsyncClient, tmp_path: Path,
) -> None:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.executescript(
            "INSERT INTO chat_sessions (id, title) VALUES (2, 'Two');"
            "INSERT INTO chat_sessions (id, title, deleted_at) VALUES (3, 'Gone', '2026-11-16');"
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (2, 'arxiv:2', 'arxiv', '2', 'Unread', 's', 'd', 'h');"
            "INSERT INTO ideas (id, source_doc, source_section, title) "
            "VALUES (5, 1, '1 Intro', 'An idea');"
            "INSERT INTO idea_sessions (idea_id, session_id) VALUES (5, 1), (5, 2), (5, 3);")
        await conn.commit()
    # An idea's note: its live idea chats.
    idea = (await client.get("/notes/target", params={"kind": "idea", "id": 5})).json()
    assert idea["shared_by"] == 2
    assert (await client.get(f"/notes/{idea['note']['id']}")).json()["shared_by"] == 2
    # A paper in the library but in no chat.
    lone = (await client.get("/notes/target", params={"kind": "paper", "id": 2})).json()
    assert lone["shared_by"] == 0
