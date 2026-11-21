"""Table blocks: accepted, validated, indexed as text (Plan H5b.1)."""
from copy import deepcopy
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes.models import check_table, table_rows
from paperhub.notes.store import block_text

pytestmark = pytest.mark.asyncio

TID = "5a070443-97b8-40ae-bbcc-ff555ee02d52"
TABLE: dict[str, Any] = {
    "type": "tableContent",
    "columnWidths": [None, None],
    "headerRows": 1,
    "rows": [
        {"cells": ["指標", "值"]},
        {"cells": [[{"type": "text", "text": "ORR", "styles": {"bold": True}}],
                   "62%"]},
    ],
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


async def _insert(client: AsyncClient, nid: int, block: dict[str, Any],
                  rev: int = 0) -> Any:
    return await client.post(f"/notes/{nid}/ops", json={
        "base_revision": rev,
        "ops": [{"op": "insert", "block_id": TID, "block": block}]})


async def test_table_insert_roundtrips_and_is_indexed(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": TABLE})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == [TID]
    got = (await client.get(f"/notes/{nid}")).json()["blocks"]
    blk = next(b for b in got if b["id"] == TID)
    assert blk["content"] == TABLE
    assert blk["text"] == "指標 | 值\nORR | 62%"
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute(
            "SELECT b.text FROM note_blocks_fts f JOIN note_blocks b "
            "ON b.rowid = f.rowid WHERE note_blocks_fts MATCH 'ORR'"
        ) as cur,
    ):
        rows = await cur.fetchall()
    assert [r[0] for r in rows] == ["指標 | 值\nORR | 62%"]


def _bad(mut: Any) -> dict[str, Any]:
    t = deepcopy(TABLE)
    mut(t)
    return t


CELL = {"type": "tableCell", "props": {}, "content": [{"type": "text", "text": "x"}]}


@pytest.mark.parametrize("table", [
    _bad(lambda t: t["rows"][1]["cells"].pop()),  # ragged
    _bad(lambda t: t.update(headerRows=2)),
    _bad(lambda t: t["rows"][0]["cells"].__setitem__(
        0, {**CELL, "props": {"backgroundColor": "red"}})),
    _bad(lambda t: t["rows"][0]["cells"].__setitem__(
        0, {**CELL, "props": {"colspan": 2}})),
    _bad(lambda t: t.update(type="other")),
    _bad(lambda t: t["rows"][0]["cells"].__setitem__(
        0, [{"type": "markCite", "props": {"markId": "", "text": ""}}])),
    _bad(lambda t: t.update(columnWidths=[None])),
], ids=["ragged", "headerRows2", "bg", "colspan", "type", "markCite", "widths"])
async def test_invalid_tables_are_rejected(client: AsyncClient, table: Any) -> None:
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": table})
    assert r.status_code == 422


async def test_table_needs_dict_content(client: AsyncClient) -> None:
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": "plain"})
    assert r.status_code == 422


async def test_paragraph_is_unchanged(client: AsyncClient) -> None:
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "paragraph", "content": "hello"})
    assert r.status_code == 200
    r = await client.post(f"/notes/{nid}/ops", json={
        "base_revision": 1,
        "ops": [{"op": "update", "block_id": TID,
                 "block": {"type": "paragraph", "content": "bye"}}]})
    assert r.status_code == 200
    blk = next(b for b in (await client.get(f"/notes/{nid}")).json()["blocks"]
               if b["id"] == TID)
    assert blk["text"] == "bye"


async def test_helpers() -> None:
    assert check_table(TABLE) == TABLE
    rows = table_rows(TABLE)
    assert [[c[0]["text"] for c in r] for r in rows] == [["指標", "值"], ["ORR", "62%"]]
    assert block_text("table", {}, TABLE) == "指標 | 值\nORR | 62%"
    cell = {"type": "tableCell", "props": {"textAlignment": "right"},
            "content": [{"type": "text", "text": "z", "styles": {}}]}
    t = {"type": "tableContent", "columnWidths": [None],
         "rows": [{"cells": [cell]}]}
    assert check_table(t) == t
    assert table_rows(t) == [[[{"type": "text", "text": "z", "styles": {}}]]]


# ------------------------------------------------- caption and notes (Task 13)

async def test_caption_and_notes_are_stored_and_searchable(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid = await _note(client)
    r = await _insert(client, nid, {
        "type": "table", "content": TABLE,
        "props": {"caption": "Response rates", "notes": "ORR, objective response; $p<0.05$"}})
    assert r.status_code == 200, r.text
    blk = next(b for b in (await client.get(f"/notes/{nid}")).json()["blocks"]
               if b["id"] == TID)
    assert blk["props"] == {"caption": "Response rates",
                            "notes": "ORR, objective response; $p<0.05$"}
    # The caption first, the notes last.
    assert blk["text"] == ("Response rates\n指標 | 值\nORR | 62%\n"
                           "ORR, objective response; $p<0.05$")
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute(
            "SELECT b.id FROM note_blocks_fts f JOIN note_blocks b "
            "ON b.rowid = f.rowid WHERE note_blocks_fts MATCH 'rates'"
        ) as cur,
    ):
        assert [r[0] for r in await cur.fetchall()] == [TID]


@pytest.mark.parametrize("props", [
    {"textColor": "red"},
    {"caption": 3},
    {"notes": ["a"]},
    {"caption": "x" * 2001},
], ids=["other", "caption-int", "notes-list", "too-long"])
async def test_other_table_props_are_rejected(client: AsyncClient,
                                              props: dict[str, Any]) -> None:
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": TABLE,
                                    "props": props})
    assert r.status_code == 422


async def test_block_text_with_caption_and_notes() -> None:
    assert block_text("table", {"caption": "C", "notes": ""}, TABLE) == (
        "C\n指標 | 值\nORR | 62%")
    assert block_text("table", {"notes": "N"}, TABLE) == "指標 | 值\nORR | 62%\nN"
