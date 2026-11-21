"""The core agent reads and writes table blocks (H5b.1 Task 5)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.core.note_tools import register_note_tools
from paperhub.agents.core.tools import (
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    TurnEvidence,
)
from paperhub.llm.adapter import ToolCall
from paperhub.notes.models import table_rows
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note, list_blocks
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio

GFM = "| a | b |\n|---|---|\n| 1 | 2 [chunk:5] [chunk:999] |"


def _ctx(conn: aiosqlite.Connection, tracer: Tracer) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=[], history=[], state={})


async def _call(ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    reg = ToolRegistry()
    register_note_tools(reg)
    return await reg.run(ctx, ToolCall(id="c1", name=name, arguments=args))


async def _chat_note(conn: aiosqlite.Connection) -> int:
    return (await get_or_create_note(
        conn, target_kind="session", target_id="1", title="New chat",
        session_id=1)).id


def _bold(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text, "styles": {"bold": True}}]


RICH = {"type": "tableContent", "columnWidths": [None, None], "headerRows": 1,
        "rows": [
            {"cells": ["k", "v"]},
            {"cells": [_bold("ORR"), [
                {"type": "text", "text": "62% ", "styles": {}},
                {"type": "markCite", "props": {"markId": "m1", "text": "src"}}]]},
            {"cells": ["x", "y"]}]}


def _cell_texts(content: Any) -> list[list[str]]:
    return [["".join(r.get("text", "") for r in cell) for cell in row]
            for row in table_rows(content)]


async def test_edit_note_offers_a_table_as_a_gfm_string() -> None:
    reg = ToolRegistry()
    register_note_tools(reg)
    spec = reg.get("edit_note")
    assert "GFM pipe table" in spec.description
    item = spec.parameters["properties"]["ops"]["items"]
    assert item["properties"]["block"]["properties"]["content"] == {
        "type": "string"}


async def test_read_note_shows_a_table_as_gfm(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="user", ops=[
        Op(op="insert", block=BlockIn(type="table", content={
            "type": "tableContent", "columnWidths": [None, None],
            "headerRows": 1, "rows": [{"cells": ["指標", "值"]},
                                      {"cells": [_bold("ORR"), "62%"]}]}))])
    bid = res.applied[0]
    out = await _call(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    assert (f'<note_block id="{bid}" type="table" author="user">'
            "| 指標 | 值 |\n| --- | --- |\n| ORR | 62% |</note_block>") in out.content


async def test_read_note_shows_cell_citations_as_tags(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await apply_ops(migrated_db, nid, base_revision=0, actor="agent", ops=[
        Op(op="insert", block=BlockIn(type="table", content=RICH))])
    out = await _call(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    assert '| ORR | 62% <mark_cite mark="m1">src</mark_cite> |' in out.content


async def test_insert_a_gfm_table(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    ctx = _ctx(migrated_db, fake_tracer)
    ctx.evidence = TurnEvidence(chunks={5})
    out = await _call(ctx, "edit_note", note="chat", base_revision=0, ops=[
        {"op": "insert", "block": {"type": "table", "content": GFM}}])
    assert not out.record.get("error"), out.content
    [block] = await list_blocks(migrated_db, nid)
    assert block.type == "table" and block.author == "ai"
    assert isinstance(block.content, dict) and block.content["headerRows"] == 1
    # An unread citation in a cell is marked, as in a paragraph.
    assert _cell_texts(block.content) == [
        ["a", "b"], ["1", "2 [chunk:5] [unsourced:not_read]"]]
    assert out.record["citations_removed"] == {"chunks": [999]}
    summary = out.record["summary"]
    assert summary["tables_added"] == [{"rows": 2, "cols": 2}]
    assert summary["added"] == 1
    assert out.events[0][1]["summary"] == summary


async def test_tables_added_replayed_from_the_trace(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.api.chat import _side_effects_from_trace

    await _chat_note(migrated_db)
    out = await _call(_ctx(migrated_db, fake_tracer), "edit_note", note="chat",
                      base_revision=0, ops=[
                          {"op": "insert", "block": {"type": "table",
                                                     "content": GFM}}])
    events = await _side_effects_from_trace(migrated_db, 1)
    assert events[0]["data"]["summary"]["tables_added"] == [
        {"rows": 2, "cols": 2}]
    assert events[0]["data"] == out.events[0][1]


async def test_no_tables_added_without_a_table(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    await _chat_note(migrated_db)
    out = await _call(_ctx(migrated_db, fake_tracer), "edit_note", note="chat",
                      base_revision=0, ops=[
                          {"op": "insert", "block": {"type": "paragraph",
                                                     "content": "x"}}])
    assert "tables_added" not in out.record["summary"]


async def test_malformed_gfm_is_rejected_with_its_reason(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    out = await _call(_ctx(migrated_db, fake_tracer), "edit_note", note="chat",
                      base_revision=0, ops=[
                          {"op": "insert", "block": {
                              "type": "table", "content": "| a | b |\n| 1 | 2 |"}},
                          {"op": "insert", "block": {
                              "type": "paragraph", "content": "kept"}}])
    data = json.loads(out.content)
    assert data["rejected"][0]["reason"] == "invalid"
    assert data["rejected"][0]["op"] == 0
    assert "delimiter row" in data["rejected"][0]["hint"]
    assert [b.text for b in await list_blocks(migrated_db, nid)] == ["kept"]


async def test_update_keeps_unchanged_cells_runs(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="agent", ops=[
        Op(op="insert", block=BlockIn(type="table", content=RICH))])
    bid = res.applied[0]
    ctx = _ctx(migrated_db, fake_tracer)
    await _call(ctx, "read_note", note="chat")
    # The model echoes what it read (the citation tag) and changes one cell.
    out = await _call(ctx, "edit_note", note="chat", base_revision=res.revision,
                      ops=[{"op": "update", "block_id": bid, "block": {
                          "type": "table", "content":
                          "| k | v |\n|---|---|\n"
                          '| ORR | 62% <mark_cite mark="m1">src</mark_cite> |\n'
                          "| x | z |"}}])
    assert not out.record.get("error"), out.content
    assert out.record["applied"] == [bid]
    [block] = await list_blocks(migrated_db, nid)
    rows = table_rows(block.content)  # type: ignore[arg-type]
    assert rows[1][0] == _bold("ORR")
    assert rows[1][1][1]["type"] == "markCite"
    assert rows[1][1][1]["props"]["markId"] == "m1"
    assert _cell_texts(block.content) == [
        ["k", "v"], ["ORR", "62% "], ["x", "z"]]
    assert "tables_added" not in out.record["summary"]
    assert out.record["summary"]["updated"] == 1


async def test_update_of_a_changed_cell_keeps_its_unchanged_runs(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="agent", ops=[
        Op(op="insert", block=BlockIn(type="table", content=RICH))])
    bid = res.applied[0]
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _call(ctx, "edit_note", note="chat", base_revision=res.revision,
                      ops=[{"op": "update", "block_id": bid, "block": {
                          "type": "table", "content":
                          "| k | v |\n|---|---|\n| ORR | 64% src |\n| x | y |"}}])
    assert not out.record.get("error"), out.content
    [block] = await list_blocks(migrated_db, nid)
    cell = table_rows(block.content)[1][1]  # type: ignore[arg-type]
    assert any(r.get("type") == "markCite" for r in cell)
    assert out.record["inline"][0]["block_id"] == bid
