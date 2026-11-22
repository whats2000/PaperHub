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
from paperhub.notes.models import (
    GridCell,
    check_table,
    column_aligns,
    header_depth,
    table_grid,
    table_rows,
)
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
    _bad(lambda t: t.update(headerRows=3)),
    _bad(lambda t: t["rows"][0]["cells"].__setitem__(
        0, {**CELL, "props": {"backgroundColor": "red"}})),
    _bad(lambda t: t.update(type="other")),
    _bad(lambda t: t["rows"][0]["cells"].__setitem__(
        0, [{"type": "markCite", "props": {"markId": "", "text": ""}}])),
    _bad(lambda t: t.update(columnWidths=[None])),
], ids=["ragged", "headerRows3", "bg", "type", "markCite", "widths"])
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


# ------------------------------------------- a suggestion on a math table

MATH_TABLE: dict[str, Any] = {
    "type": "tableContent",
    "columnWidths": [None, None],
    "headerRows": 1,
    "rows": [
        {"cells": ["方法", "更新"]},
        {"cells": ["RoboTTT", [
            {"type": "text", "text": "fast weights ", "styles": {}},
            {"type": "inlineMath", "props": {"latex": "W_t"}}]]},
        {"cells": ["LoRA", "待查"]},
    ],
}


async def _agent_update(tmp_path: Path, nid: int, rev: int,
                        content: dict[str, Any],
                        props: dict[str, Any] | None = None) -> str:
    from paperhub.notes.ops import BlockIn, Op, apply_ops
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        res = await apply_ops(conn, nid, base_revision=rev, actor="agent", ops=[
            Op(op="update", block_id=TID,
               block=BlockIn(type="table", content=content,
                             props=props or {}))])
    assert res.change_set_id is not None
    return res.change_set_id


async def _agent_insert(tmp_path: Path, nid: int, block: dict[str, Any]) -> int:
    from paperhub.notes.ops import BlockIn, Op, apply_ops
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        res = await apply_ops(conn, nid, base_revision=0, actor="agent", ops=[
            Op(op="insert", block_id=TID, block=BlockIn(**block))])
    return res.revision


async def test_a_suggestion_on_a_math_table_shows_what_accept_writes(
    client: AsyncClient, tmp_path: Path,
) -> None:
    """The agent reads inline math as ``$…$`` and writes it back as text:
    the suggestion is shown as Accept lays it over the table, so a cell it
    did not change keeps its math (and reads as unchanged)."""
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": MATH_TABLE})
    rev = r.json()["revision"]
    await _agent_update(tmp_path, nid, rev, {
        "type": "tableContent", "headerRows": 1,
        "rows": [{"cells": ["方法", "更新"]},
                 {"cells": ["RoboTTT", "fast weights $W_t$"]},
                 {"cells": ["LoRA", "無"]}]})
    detail = (await client.get(f"/notes/{nid}")).json()
    [sug] = detail["suggestions"]
    rows = table_rows(sug["proposed"]["content"])
    assert rows[1][1] == MATH_TABLE["rows"][1]["cells"][1]
    assert [r["text"] for r in rows[2][1]] == ["無"]
    # Accept writes the same.
    res = await client.post(f"/notes/suggestions/{sug['id']}",
                            json={"accept": True})
    assert res.status_code == 200, res.text
    [block] = (await client.get(f"/notes/{nid}")).json()["blocks"]
    assert table_rows(block["content"])[1][1] == rows[1][1]


EDITED_MATH = {
    "type": "tableContent", "headerRows": 1,
    "rows": [{"cells": ["方法", "更新"]},
             {"cells": ["RoboTTT", "fast weights $W_t$"]},
             {"cells": ["LoRA", "無"]}]}


async def test_the_change_card_gets_a_suggested_table_before_and_after(
    client: AsyncClient, tmp_path: Path,
) -> None:
    """The chat's change card shows a table edit cell by cell: it gets the
    table now and as Accept would write it, not only their flat text."""
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": MATH_TABLE,
                                    "props": {"caption": "Old"}})
    cs = await _agent_update(tmp_path, nid, r.json()["revision"],
                             EDITED_MATH, {"caption": "New"})
    detail = (await client.get(f"/notes/change-sets/{cs}")).json()
    [change] = detail["changes"]
    assert change["kind"] == "suggested"
    table = change["table"]
    assert table["before"]["props"] == {"caption": "Old"}
    assert table["after"]["props"] == {"caption": "New"}
    assert table_rows(table["before"]["content"]) == table_rows(MATH_TABLE)
    after = table_rows(table["after"]["content"])
    assert after[1][1] == MATH_TABLE["rows"][1]["cells"][1]
    assert [x["text"] for x in after[2][1]] == ["無"]


async def test_the_change_card_gets_an_updated_table_before_and_after(
    client: AsyncClient, tmp_path: Path,
) -> None:
    """An applied edit of the agent's own table: both sides carry the
    caption and the notes ("" for none), so a removal reads as one."""
    nid = await _note(client)
    rev = await _agent_insert(tmp_path, nid, {
        "type": "table", "content": MATH_TABLE, "props": {"caption": "Old"}})
    cs = await _agent_update(tmp_path, nid, rev, EDITED_MATH)
    detail = (await client.get(f"/notes/change-sets/{cs}")).json()
    [change] = detail["changes"]
    assert change["kind"] == "updated"
    table = change["table"]
    assert table["before"]["props"] == {"caption": "Old", "notes": ""}
    assert table["after"]["props"] == {"caption": "Old", "notes": ""}
    after = table_rows(table["after"]["content"])
    assert after[1][1] == MATH_TABLE["rows"][1]["cells"][1]
    assert [x["text"] for x in after[2][1]] == ["無"]


# ------------------------------------------------------ merged cells (H5b.1)

def _c(text: str, **props: Any) -> dict[str, Any]:
    return {"type": "tableCell", "props": props,
            "content": [{"type": "text", "text": text, "styles": {}}] if text else []}


MERGED: dict[str, Any] = {
    "type": "tableContent",
    "columnWidths": [None, None, None],
    "headerRows": 2,
    "rows": [
        {"cells": [_c("Method", rowspan=2),
                   _c("成功恢復（每格 n = 20）", colspan=2, textAlignment="center")]},
        {"cells": [_c("Roof 擾動", textAlignment="center"),
                   _c("Tire 擾動", textAlignment="center")]},
        {"cells": [_c("GDN"), _c("18"), _c("17")]},
        {"cells": [_c("TTT"), _c("15"), _c("14")]},
        {"cells": [_c("LoRA"), _c("9"), _c("8")]},
    ],
}


async def test_merged_table_is_accepted_and_roundtrips(client: AsyncClient) -> None:
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": MERGED})
    assert r.status_code == 200, r.text
    blk = next(b for b in (await client.get(f"/notes/{nid}")).json()["blocks"]
               if b["id"] == TID)
    assert blk["content"] == MERGED
    assert blk["text"] == ("Method | 成功恢復（每格 n = 20）\nRoof 擾動 | Tire 擾動\n"
                           "GDN | 18 | 17\nTTT | 15 | 14\nLoRA | 9 | 8")


def test_table_grid_of_a_merged_table() -> None:
    grid, rows, cols = table_grid(MERGED)
    assert (rows, cols) == (5, 3)
    assert grid[1][0] == (0, 0)
    assert grid[0][2] == (0, 1)
    assert grid[0][0] == GridCell(row=0, col=0, rowspan=2, colspan=1, index=0)
    assert grid[1][1] == GridCell(row=1, col=1, rowspan=1, colspan=1, index=0)


def test_header_depth() -> None:
    assert header_depth({**MERGED, "headerRows": 1}) == 2
    assert header_depth({**MERGED, "headerRows": 0}) == 0
    assert header_depth(TABLE) == 1


def _span(t: dict[str, Any], r: int, i: int, **props: Any) -> None:
    t["rows"][r]["cells"][i]["props"].update(props)


@pytest.mark.parametrize("mut", [
    lambda t: _span(t, 2, 1, colspan=3),          # past the width
    lambda t: _span(t, 1, 0, rowspan=2) or _span(t, 2, 0, colspan=2),  # overlap
    lambda t: _span(t, 2, 0, rowspan=0),
    lambda t: _span(t, 2, 0, colspan="2"),
    lambda t: t.update(headerRows=6),
], ids=["past-width", "overlap", "rowspan0", "colspan-str", "headerRows6"])
async def test_bad_merges_are_rejected_with_a_reason(client: AsyncClient,
                                                     mut: Any) -> None:
    t = deepcopy(MERGED)
    mut(t)
    with pytest.raises(ValueError):
        check_table(t)
    nid = await _note(client)
    r = await _insert(client, nid, {"type": "table", "content": t})
    assert r.status_code == 422
    assert r.text


def test_column_aligns_reads_a_column_under_a_span_from_a_later_row() -> None:
    t = {"type": "tableContent", "headerRows": 1, "rows": [
        {"cells": [_c("a"), _c("b", colspan=2, textAlignment="center")]},
        {"cells": [_c("1"), _c("2", textAlignment="right"),
                   _c("3", textAlignment="center")]},
    ]}
    assert column_aligns(t) == ["", "right", "center"]
