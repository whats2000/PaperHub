"""Tests for the core agent's edit_note tool (H5b.1 Task 9)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.core.note_tools import register_note_tools
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.papers import paper_note
from paperhub.notes.store import get_note, get_or_create_note, list_blocks
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


def _ctx(conn: aiosqlite.Connection, tracer: Tracer,
         enabled: list[tuple[int, str]] | None = None) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=enabled or [],
        history=[], state={})


async def _edit(ctx: ToolContext, **args: Any) -> ToolOutcome:
    reg = ToolRegistry()
    register_note_tools(reg)
    return await reg.run(ctx, ToolCall(id="c1", name="edit_note", arguments=args))


def _ins(text: str, **place: Any) -> dict[str, Any]:
    return {"op": "insert", "block": {
        "type": "paragraph", "content": [{"type": "text", "text": text}]}, **place}


async def _paper(conn: aiosqlite.Connection) -> int:
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, "
        "source_dir_path, html_path, sections_json) "
        "VALUES ('arxiv:p', 'arxiv', 'p', 'Paper', '/x', '/x', '/x', ?)",
        (json.dumps([{"name": "Introduction"}, {"name": "Method"}]),))
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    await conn.commit()
    return int(row[0])


async def _chat_note(conn: aiosqlite.Connection) -> int:
    return (await get_or_create_note(
        conn, target_kind="session", target_id="1", title="New chat",
        session_id=1)).id


async def test_edit_inserts_under_section_name(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid = await _paper(migrated_db)
    note = await paper_note(migrated_db, pid)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "Paper")])
    out = await _edit(ctx, note=f"paper:{pid}", base_revision=note.revision,
                      ops=[_ins("key point", under_heading="method")])
    assert not out.record.get("error"), out.content
    data = json.loads(out.content)
    assert len(data["applied"]) == 2  # the new section and the paragraph
    blocks = await list_blocks(migrated_db, note.id)
    sec = next(b for b in blocks if b.type == "section")
    assert sec.section_key == "Method"
    para = next(b for b in blocks if b.type == "paragraph")
    assert para.author == "ai" and para.text == "key point"
    assert blocks.index(para) > blocks.index(sec)
    assert out.events[0][0] == "notes_changed"
    assert out.events[0][1]["summary"]["section"] == "Method"


async def test_insert_without_place_invalid(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    first = await apply_ops(migrated_db, nid, base_revision=0, ops=[
        Op(op="insert", block=BlockIn(type="paragraph", content="mine"))],
        actor="user")
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _edit(ctx, note="chat", base_revision=first.revision,
                      ops=[_ins("loose")])
    data = json.loads(out.content)
    assert data["applied"] == []
    assert data["rejected"][0]["reason"] == "invalid"
    assert [b.text for b in await list_blocks(migrated_db, nid)] == ["mine"]
    assert out.events == []


async def test_insert_into_empty_note_needs_no_place(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """An empty note has one place to write, so no anchor is needed (a new
    chat's note would otherwise be unwritable)."""
    nid = await _chat_note(migrated_db)
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _edit(ctx, note="chat", base_revision=0,
                      ops=[_ins("first"), _ins("second")])
    data = json.loads(out.content)
    assert data["rejected"] == []
    assert [b.text for b in await list_blocks(migrated_db, nid)] == [
        "first", "second"]


async def test_user_block_update_is_suggestion(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="user", ops=[
        Op(op="insert", block=BlockIn(type="paragraph", content="mine"))])
    bid = res.applied[0]
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _edit(ctx, note="chat", base_revision=res.revision, ops=[
        {"op": "update", "block_id": bid, "block": {
            "type": "paragraph", "content": "theirs"}}])
    data = json.loads(out.content)
    assert data["applied"] == [] and len(data["suggested"]) == 1
    assert out.events[0][1]["summary"]["suggested"] == 1
    assert (await list_blocks(migrated_db, nid))[0].text == "mine"


async def test_event_summary_counts(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="agent", ops=[
        Op(op="insert", block=BlockIn(type="heading", content="H")),
        Op(op="insert", block=BlockIn(type="paragraph", content="old")),
        Op(op="insert", block=BlockIn(type="paragraph", content="gone"))])
    h, old, gone = res.applied
    ctx = _ctx(migrated_db, fake_tracer)
    reg = ToolRegistry()
    register_note_tools(reg)
    await reg.run(ctx, ToolCall(id="r", name="read_note", arguments={"note": "chat"}))
    out = await _edit(ctx, note="chat", base_revision=res.revision, ops=[
        _ins("new a", under_heading=h), _ins("new b", after=old),
        {"op": "update", "block_id": old, "block": {
            "type": "paragraph", "content": "fresh"}},
        {"op": "delete", "block_id": gone},
        {"op": "delete", "block_id": "nope"}])
    ev = out.events[0][1]
    assert ev["summary"] == {"added": 2, "updated": 1, "deleted": 1,
                             "suggested": 0, "rejected": 1, "section": None}
    assert ev["note_id"] == nid and ev["note_title"] == "New chat"
    assert ev["revision"] == res.revision + 1 and ev["change_set_id"]
    # The agent's own write does not count as a user change afterwards.
    async with migrated_db.execute(
        "SELECT revision FROM note_agent_reads WHERE note_id = ?", (nid,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == ev["revision"]


async def test_card_replayed_after_reload(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.agents.core.agent import REPLAYED_UI_EVENTS
    from paperhub.api.chat import _SIDE_EFFECT_EVENTS, _side_effects_from_trace

    assert "notes_changed" in REPLAYED_UI_EVENTS
    assert "notes_changed" in _SIDE_EFFECT_EVENTS
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="agent", ops=[
        Op(op="insert", block=BlockIn(type="heading", content="H"))])
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _edit(ctx, note="chat", base_revision=res.revision,
                      ops=[_ins("x", under_heading=res.applied[0])])
    assert out.events
    # The traced step the registry recorded is what a stopped turn rebuilds from.
    events = await _side_effects_from_trace(migrated_db, 1)
    assert [e["event"] for e in events] == ["notes_changed"]
    assert events[0]["data"] == out.events[0][1]


async def test_trace_records_outcome(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="agent", ops=[
        Op(op="insert", block=BlockIn(type="heading", content="H"))])
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _edit(ctx, note="chat", base_revision=res.revision,
                      ops=[_ins("x", under_heading=res.applied[0])])
    rec = out.record
    assert rec["tool"] == "notes:edit" and rec["note_id"] == nid
    assert rec["base_revision"] == res.revision
    assert rec["revision"] == res.revision + 1
    assert len(rec["applied"]) == 1 and rec["suggested"] == []
    assert rec["rejected"] == [] and rec["ops"][0]["op"] == "insert"
    note = await get_note(migrated_db, nid)
    assert note is not None and note.revision == rec["revision"]


async def _read(ctx: ToolContext, note: str) -> None:
    reg = ToolRegistry()
    register_note_tools(reg)
    await reg.run(ctx, ToolCall(id="r", name="read_note", arguments={"note": note}))


async def _history_actor(conn: aiosqlite.Connection, block_id: str) -> str:
    async with conn.execute(
        "SELECT actor FROM note_block_history WHERE block_id = ? "
        "ORDER BY id LIMIT 1", (block_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def test_new_section_is_part_of_the_change_set(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:

    pid = await _paper(migrated_db)
    note = await paper_note(migrated_db, pid)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "Paper")])
    await _read(ctx, f"paper:{pid}")
    out = await _edit(ctx, note=f"paper:{pid}", base_revision=note.revision,
                      ops=[_ins("a", under_heading="Method"),
                           _ins("b", under_heading="method")])
    ev = out.events[0][1]
    assert ev["revision"] == note.revision + 1  # one revision, not two
    assert ev["summary"]["added"] == 2 and ev["summary"]["section"] == "Method"
    blocks = await list_blocks(migrated_db, note.id)
    secs = [b for b in blocks if b.type == "section"]
    assert len(secs) == 1
    assert await _history_actor(migrated_db, secs[0].id) == "agent"
    async with migrated_db.execute(
        "SELECT revision FROM note_agent_reads WHERE note_id = ?", (note.id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == ev["revision"]
    from paperhub.notes.ops import undo_change_set
    await undo_change_set(migrated_db, ev["change_set_id"])
    assert await list_blocks(migrated_db, note.id) == []


async def test_new_section_comes_in_paper_order(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid = await _paper(migrated_db)
    note = await paper_note(migrated_db, pid)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "Paper")])
    await _edit(ctx, note=f"paper:{pid}", base_revision=note.revision,
                ops=[_ins("m", under_heading="Method")])
    cur = (await get_note(migrated_db, note.id))
    assert cur is not None
    await _edit(ctx, note=f"paper:{pid}", base_revision=cur.revision,
                ops=[_ins("i", under_heading="Introduction")])
    keys = [b.section_key for b in await list_blocks(migrated_db, note.id)
            if b.type == "section"]
    assert keys == ["Introduction", "Method"]


async def test_no_section_left_for_invalid_ops(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid = await _paper(migrated_db)
    note = await paper_note(migrated_db, pid)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "Paper")])
    out = await _edit(ctx, note=f"paper:{pid}", base_revision=note.revision,
                      ops=[_ins("x", under_heading="No such section"),
                           _ins("loose")])
    data = json.loads(out.content)
    assert data["applied"] == [] and len(data["rejected"]) == 2
    assert await list_blocks(migrated_db, note.id) == []
    n = await get_note(migrated_db, note.id)
    assert n is not None and n.revision == note.revision


async def test_existing_section_is_reused(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.notes.papers import ensure_section

    pid = await _paper(migrated_db)
    note = await paper_note(migrated_db, pid)
    sec = await ensure_section(migrated_db, note.id, pid, "Method")
    n = await get_note(migrated_db, note.id)
    assert n is not None
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "Paper")])
    out = await _edit(ctx, note=f"paper:{pid}", base_revision=n.revision,
                      ops=[_ins("again", under_heading="Method")])
    assert out.events[0][1]["summary"]["added"] == 1
    secs = [b for b in await list_blocks(migrated_db, note.id)
            if b.type == "section"]
    assert [b.id for b in secs] == [sec]


async def test_summary_counts_move_and_before(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="agent", ops=[
        Op(op="insert", block=BlockIn(type="paragraph", content="a")),
        Op(op="insert", block=BlockIn(type="paragraph", content="b"))])
    a, b = res.applied
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _edit(ctx, note="chat", base_revision=res.revision, ops=[
        _ins("front", before=a), {"op": "move", "block_id": b, "before": a}])
    assert out.events[0][1]["summary"] == {
        "added": 1, "updated": 1, "deleted": 0, "suggested": 0,
        "rejected": 0, "section": None}


async def test_suggestion_only_card_replayed(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.api.chat import _side_effects_from_trace

    nid = await _chat_note(migrated_db)
    res = await apply_ops(migrated_db, nid, base_revision=0, actor="user", ops=[
        Op(op="insert", block=BlockIn(type="paragraph", content="mine"))])
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _edit(ctx, note="chat", base_revision=res.revision, ops=[
        {"op": "delete", "block_id": res.applied[0]}])
    assert json.loads(out.content)["applied"] == []
    assert out.events[0][1]["change_set_id"]
    events = await _side_effects_from_trace(migrated_db, 1)
    assert [e["data"] for e in events] == [out.events[0][1]]


async def test_bad_arguments_are_clean_errors(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.agents.core.note_tools import _edit_note

    await _chat_note(migrated_db)
    ctx = _ctx(migrated_db, fake_tracer)
    for args in ({}, {"note": "chat"}, {"note": "chat", "base_revision": 0},
                 {"ops": [], "base_revision": 0}):
        out = await _edit_note(ctx, args)
        assert out.record.get("error"), args
    out = await _edit(ctx, note="chat", base_revision=99, ops=[
        _ins("x", under_heading="h")])
    assert out.record.get("error") and "ahead" in out.content


async def test_divider_before_is_advertised_and_applied(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """The schema offers divider_before, so the agent can start an appended
    block as a new note within the section."""
    reg = ToolRegistry()
    register_note_tools(reg)
    item = reg.get("edit_note").parameters["properties"]["ops"]["items"]
    assert item["properties"]["divider_before"] == {"type": "boolean"}
    pid = await _paper(migrated_db)
    note = await paper_note(migrated_db, pid)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "Paper")])
    out = await _edit(ctx, note=f"paper:{pid}", base_revision=note.revision,
                      ops=[_ins("first", under_heading="Method")])
    rev = json.loads(out.content)["revision"]
    out = await _edit(ctx, note=f"paper:{pid}", base_revision=rev,
                      ops=[_ins("second", under_heading="Method",
                                divider_before=True)])
    assert not out.record.get("error"), out.content
    types = [(b.type, b.text) for b in await list_blocks(migrated_db, note.id)]
    assert types[-3:] == [("paragraph", "first"), ("divider", ""),
                          ("paragraph", "second")]


async def test_block_content_is_offered_as_text() -> None:
    """The model sends a block's text as a string (it guessed {"text": ...}
    objects against an untyped schema in the core_agent/v9 eval)."""
    reg = ToolRegistry()
    register_note_tools(reg)
    item = reg.get("edit_note").parameters["properties"]["ops"]["items"]
    assert item["properties"]["block"]["properties"]["content"] == {
        "type": "string"}
    assert "divider_before" in reg.get("edit_note").description
