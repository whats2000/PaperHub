"""``POST /notes/{id}/ops`` ``update``: props patch the stored ones.

The editor sends a block's full props; any other client may send only the
props it changes. A key the update leaves out keeps its value, ``null``
removes it, and a mark block's ``markId`` (the mark it shows) can be
neither removed nor changed.
"""
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio

TID = "5a070443-97b8-40ae-bbcc-ff555ee02d52"
QID = "6b181554-a8c9-41bf-8cdd-0066f13e3e63"
TABLE: dict[str, Any] = {
    "type": "tableContent", "columnWidths": [None, None], "headerRows": 1,
    "rows": [{"cells": ["k", "v"]}, {"cells": ["a", "1"]}],
}


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


async def _note(client: AsyncClient) -> int:
    r = await client.post("/notes/target", params={"kind": "session", "id": 1})
    return int(r.json()["note"]["id"])


async def _ops(client: AsyncClient, nid: int, rev: int,
               ops: list[dict[str, Any]]) -> dict[str, Any]:
    r = await client.post(f"/notes/{nid}/ops",
                          json={"base_revision": rev, "ops": ops})
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _block(client: AsyncClient, nid: int, bid: str) -> dict[str, Any]:
    blocks = (await client.get(f"/notes/{nid}")).json()["blocks"]
    return next(b for b in blocks if b["id"] == bid)


async def _table(client: AsyncClient) -> tuple[int, int]:
    nid = await _note(client)
    res = await _ops(client, nid, 0, [{"op": "insert", "block_id": TID, "block": {
        "type": "table", "content": TABLE,
        "props": {"caption": "Rates", "notes": "n = 20"}}}])
    return nid, int(res["revision"])


async def test_an_update_without_props_keeps_the_caption_and_notes(
    client: AsyncClient,
) -> None:
    nid, rev = await _table(client)
    edited = {**TABLE, "rows": [{"cells": ["k", "v"]}, {"cells": ["a", "2"]}]}
    res = await _ops(client, nid, rev, [{"op": "update", "block_id": TID,
                                         "block": {"type": "table", "content": edited}}])
    assert res["applied"] == [TID]
    block = await _block(client, nid, TID)
    assert block["props"] == {"caption": "Rates", "notes": "n = 20"}


async def test_null_removes_a_prop_and_a_given_value_replaces_it(
    client: AsyncClient,
) -> None:
    nid, rev = await _table(client)
    await _ops(client, nid, rev, [{"op": "update", "block_id": TID, "block": {
        "type": "table", "content": TABLE,
        "props": {"caption": "Rates (%)", "notes": None}}}])
    block = await _block(client, nid, TID)
    assert block["props"] == {"caption": "Rates (%)"}
    assert "n = 20" not in block["text"]


async def test_full_props_from_the_editor_still_set_them_all(
    client: AsyncClient,
) -> None:
    nid = await _note(client)
    res = await _ops(client, nid, 0, [{"op": "insert", "block_id": TID, "block": {
        "type": "heading", "props": {"level": 2}, "content": "Intro"}}])
    await _ops(client, nid, res["revision"], [{"op": "update", "block_id": TID,
                                               "block": {"type": "heading",
                                                         "props": {"level": 3},
                                                         "content": "Intro"}}])
    assert (await _block(client, nid, TID))["props"] == {"level": 3}


async def test_a_type_change_takes_the_new_props_whole(
    client: AsyncClient,
) -> None:
    nid = await _note(client)
    res = await _ops(client, nid, 0, [{"op": "insert", "block_id": TID, "block": {
        "type": "heading", "props": {"level": 2}, "content": "Intro"}}])
    await _ops(client, nid, res["revision"], [{"op": "update", "block_id": TID,
                                               "block": {"type": "paragraph",
                                                         "content": "Intro"}}])
    block = await _block(client, nid, TID)
    assert block["type"] == "paragraph"
    assert block["props"] == {}


QUOTE_PROPS = {"markId": "mk_1", "quote": "attention is all you need",
               "kind": "text", "color": "green"}


async def _quote(client: AsyncClient) -> tuple[int, int]:
    nid = await _note(client)
    res = await _ops(client, nid, 0, [{"op": "insert", "block_id": QID, "block": {
        "type": "paperQuote", "props": QUOTE_PROPS}}])
    assert res["applied"] == [QID], res
    return nid, int(res["revision"])


async def test_a_partial_update_of_a_mark_block_keeps_its_mark_id(
    client: AsyncClient,
) -> None:
    nid, rev = await _quote(client)
    res = await _ops(client, nid, rev, [{"op": "update", "block_id": QID, "block": {
        "type": "paperQuote", "props": {"quote": "attention"}}}])
    assert res["applied"] == [QID]
    props = (await _block(client, nid, QID))["props"]
    assert props["markId"] == "mk_1"
    assert props["quote"] == "attention"


@pytest.mark.parametrize("mark_id", [None, "", "mk_2"])
async def test_a_mark_block_s_mark_id_cannot_be_removed_or_changed(
    client: AsyncClient, mark_id: Any,
) -> None:
    nid, rev = await _quote(client)
    res = await _ops(client, nid, rev, [{"op": "update", "block_id": QID, "block": {
        "type": "paperQuote", "props": {**QUOTE_PROPS, "markId": mark_id}}}])
    assert res["applied"] == []
    [rej] = res["rejected"]
    assert rej["block_id"] == QID and rej["reason"] == "invalid"
    assert (await _block(client, nid, QID))["props"]["markId"] == "mk_1"
