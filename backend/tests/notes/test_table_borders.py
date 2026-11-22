"""A table's own lines: the ``borders`` prop (Plan H5b.1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes.models import GridCell, table_grid
from paperhub.notes.table_borders import (
    Borders,
    borders_of,
    dump_borders,
    edge,
    fit_borders,
    parse_borders,
    preset_lines,
)

TID = "5a070443-97b8-40ae-bbcc-ff555ee02d52"


def _table(rows: int, cols: int = 3) -> dict[str, Any]:
    return {"type": "tableContent", "columnWidths": [None] * cols,
            "headerRows": 1,
            "rows": [{"cells": [f"r{r}c{c}" for c in range(cols)]}
                     for r in range(rows)]}


def test_preset_lines() -> None:
    b = preset_lines(5, 3, 1, "paper")
    assert b.h == ("222", "111", "000", "000", "000", "222")
    assert b.v == ("0000",) * 5
    assert preset_lines(5, 3, 2, "paper").h[2] == "111"
    n = preset_lines(5, 3, 1, "notion")
    assert set(n.h) == {"111"} and set(n.v) == {"1111"} and len(n.h) == 6
    assert preset_lines(3, 2, 0, "paper").h == ("22", "00", "00", "22")


def test_dump_and_parse_roundtrip() -> None:
    b = preset_lines(3, 3, 1, "paper")
    raw = dump_borders(b)
    assert raw == ('{"preset":"paper","h":["222","111","000","222"],'
                   '"v":["0000","0000","0000"]}')
    assert parse_borders(raw) == b
    assert parse_borders(None) is None and parse_borders("") is None


@pytest.mark.parametrize("raw", [
    "x",
    json.dumps({"preset": "grid", "h": ["11", "11"], "v": ["111"]}),
    json.dumps({"preset": "paper", "h": ["13", "11"], "v": ["111"]}),
    json.dumps({"preset": "paper", "h": ["11", "11", "11"], "v": ["111"]}),
    json.dumps({"preset": "paper", "h": ["11", "1"], "v": ["111"]}),
], ids=["json", "preset", "digit", "count", "ragged"])
def test_bad_borders_raise(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_borders(raw)


def test_borders_of_defaults_to_paper() -> None:
    t = _table(3)
    assert borders_of({}, t) == preset_lines(3, 3, 1, "paper")
    notion = dump_borders(preset_lines(2, 3, 1, "notion"))
    assert borders_of({"borders": notion}, t) == preset_lines(3, 3, 1, "notion")


def test_fit_borders() -> None:
    old = preset_lines(3, 3, 1, "paper")
    four = _table(4)
    fitted = parse_borders(fit_borders({"borders": dump_borders(old)}, four)["borders"])
    assert fitted == preset_lines(4, 3, 1, "paper")
    assert fit_borders({}, four) == {}
    custom = Borders("paper", ("222", "111", "202", "222"), old.v)
    raw = dump_borders(custom)
    assert fit_borders({"borders": raw}, _table(3))["borders"] == raw


def test_edge_is_the_heaviest_segment() -> None:
    t = {"type": "tableContent", "headerRows": 1, "rows": [
        {"cells": [{"type": "tableCell", "props": {"colspan": 2}, "content": []}]},
        {"cells": ["a", "b"]}]}
    grid, _, _ = table_grid(t)
    anchor = grid[0][0]
    assert isinstance(anchor, GridCell)
    b = Borders("paper", ("02", "00", "22"), ("000", "000"))
    assert edge(b, grid, "top", anchor) == 2
    assert edge(b, grid, "bottom", anchor) == 0


# ----------------------------------------------------------------- the API

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


async def _op(client: AsyncClient, nid: int, op: str, block: dict[str, Any],
              rev: int) -> Any:
    return await client.post(f"/notes/{nid}/ops", json={
        "base_revision": rev,
        "ops": [{"op": op, "block_id": TID, "block": block}]})


async def _block(client: AsyncClient, nid: int) -> dict[str, Any]:
    blocks = (await client.get(f"/notes/{nid}")).json()["blocks"]
    return next(b for b in blocks if b["id"] == TID)


async def test_api_accepts_valid_borders_and_rejects_bad(client: AsyncClient) -> None:
    nid = await _note(client)
    raw = dump_borders(preset_lines(3, 3, 1, "notion"))
    r = await _op(client, nid, "insert",
                  {"type": "table", "content": _table(3), "props": {"borders": raw}}, 0)
    assert r.status_code == 200, r.text
    assert (await _block(client, nid))["props"]["borders"] == raw
    r = await _op(client, nid, "update",
                  {"type": "table", "content": _table(3),
                   "props": {"borders": '{"preset":"x"}'}}, 1)
    assert r.status_code == 422
    assert "preset" in r.text


async def test_a_reshape_gets_the_presets_lines(client: AsyncClient) -> None:
    nid = await _note(client)
    old = dump_borders(preset_lines(3, 3, 1, "paper"))
    await _op(client, nid, "insert",
              {"type": "table", "content": _table(3), "props": {"borders": old}}, 0)
    r = await _op(client, nid, "update",
                  {"type": "table", "content": _table(4), "props": {"borders": old}}, 1)
    assert r.status_code == 200, r.text
    got = (await _block(client, nid))["props"]["borders"]
    assert parse_borders(got) == preset_lines(4, 3, 1, "paper")
    notion = dump_borders(preset_lines(4, 3, 1, "notion"))
    await _op(client, nid, "update",
              {"type": "table", "content": _table(4), "props": {"borders": notion}}, 2)
    await _op(client, nid, "update",
              {"type": "table", "content": _table(2), "props": {"borders": notion}}, 3)
    got = (await _block(client, nid))["props"]["borders"]
    assert parse_borders(got) == preset_lines(2, 3, 1, "notion")


async def test_same_shape_keeps_custom_lines_and_text_has_none(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid = await _note(client)
    base = preset_lines(3, 3, 1, "paper")
    raw = dump_borders(Borders("paper", ("222", "111", "202", "222"), base.v))
    await _op(client, nid, "insert", {"type": "table", "content": _table(3)}, 0)
    await _op(client, nid, "update",
              {"type": "table", "content": _table(3), "props": {"borders": raw}}, 1)
    blk = await _block(client, nid)
    assert parse_borders(blk["props"]["borders"]).h[2] == "202"  # type: ignore[union-attr]
    for bad in ("preset", '"h"', "222", "0000"):
        assert bad not in blk["text"]
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute("SELECT text FROM note_blocks_fts") as cur,
    ):
        for (text,) in await cur.fetchall():
            assert "preset" not in text and "0000" not in text
