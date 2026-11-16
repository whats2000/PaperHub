"""``<notes>`` and ``<notes_changes>`` in the core agent's session state (H5b.1 Task 10)."""
from __future__ import annotations

import json
import re
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.context import build_session_state
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note
from paperhub.outbound.context import begin_turn, current_private

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fake_tracer")]  # chat session 1


def _p(text: str) -> Op:
    return Op(op="insert", block=BlockIn(
        type="paragraph", content=[{"type": "text", "text": text}]))


async def _note(conn: aiosqlite.Connection, sid: int, title: str = "My chat") -> int:
    return (await get_or_create_note(
        conn, target_kind="session", target_id=str(sid), title=title,
        session_id=sid)).id


async def _rev(conn: aiosqlite.Connection, note_id: int) -> int:
    async with conn.execute("SELECT revision FROM notes WHERE id = ?", (note_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _write(conn: aiosqlite.Connection, note_id: int, text: str, actor: str) -> None:
    await apply_ops(conn, note_id, base_revision=await _rev(conn, note_id),
                    ops=[_p(text)], actor=actor)  # type: ignore[arg-type]


async def _mark_read(conn: aiosqlite.Connection, sid: int, note_id: int) -> None:
    """The agent read the whole note at its current revision."""
    from paperhub.notes.agent_reads import record_blocks, record_contact

    rev = await _rev(conn, note_id)
    await record_contact(conn, sid, {note_id: rev})
    async with conn.execute(
        "SELECT id FROM note_blocks WHERE note_id = ?", (note_id,)) as cur:
        ids = [str(r[0]) for r in await cur.fetchall()]
    await record_blocks(conn, sid, note_id, ids, rev)


async def test_notes_index_bounded(migrated_db: aiosqlite.Connection) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "hello", "user")
    for i in range(15):
        await migrated_db.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, 'arxiv', ?, ?, '/x', '/x', '/x')", (f"k{i}", f"a{i}", f"P{i}"))
        await migrated_db.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled, added_at) "
            "SELECT 1, id, 1, ? FROM paper_content WHERE content_key = ?",
            (f"2026-01-01T00:00:{i:02d}", f"k{i}"))
    await migrated_db.commit()
    state = await build_session_state(migrated_db, 1, {})
    block = re.search(r"<notes>.*?</notes>", state, re.S)
    assert block is not None
    assert block.group(0).count("<note ") == 12
    assert 'ref="chat"' in block.group(0)
    assert 'revision="1"' in block.group(0) and 'blocks="1"' in block.group(0)
    assert "<note_title>My chat</note_title>" in block.group(0)


async def test_user_edit_appears_in_notes_changes(
    migrated_db: aiosqlite.Connection,
) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "first", "user")
    await _mark_read(migrated_db, 1, nid)
    assert "<notes_changes>" not in await build_session_state(migrated_db, 1, {})
    await _write(migrated_db, nid, "second", "user")
    async with migrated_db.execute(
        "SELECT block_id FROM note_block_history WHERE note_id = ? AND revision = 2",
        (nid,),
    ) as cur:
        bid = (await cur.fetchone())[0]  # type: ignore[index]
    state = await build_session_state(migrated_db, 1, {})
    block = re.search(r"<notes_changes>.*?</notes_changes>", state, re.S)
    assert block is not None
    assert 'note="chat"' in block.group(0) and bid in block.group(0)
    assert "first" not in block.group(0)  # ids only, never the user's words


async def test_agent_read_clears_change(migrated_db: aiosqlite.Connection) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "first", "user")
    await _mark_read(migrated_db, 1, nid)
    await _write(migrated_db, nid, "second", "user")
    assert "<notes_changes>" in await build_session_state(migrated_db, 1, {})
    await _mark_read(migrated_db, 1, nid)
    assert "<notes_changes>" not in await build_session_state(migrated_db, 1, {})


async def test_agent_own_edit_is_not_a_change(
    migrated_db: aiosqlite.Connection,
) -> None:
    nid = await _note(migrated_db, 1)
    await _mark_read(migrated_db, 1, nid)
    await _write(migrated_db, nid, "by agent", "agent")
    assert "<notes_changes>" not in await build_session_state(migrated_db, 1, {})


async def test_note_titles_are_private_material(
    migrated_db: aiosqlite.Connection,
) -> None:
    begin_turn()
    nid = await _note(migrated_db, 1, "Secret plan")
    await _write(migrated_db, nid, "x", "user")
    await build_session_state(migrated_db, 1, {})
    assert any(i.kind == "note" and "Secret plan" in i.text for i in current_private())


# --- per-block read marks (Task 12 controller ruling) ----------------------

def _tool_ctx(conn: aiosqlite.Connection, tracer: Any) -> Any:
    from unittest.mock import MagicMock

    from paperhub.agents.core.tools import ToolContext

    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=[], history=[], state={})


async def _tool(ctx: Any, name: str, **args: Any) -> Any:
    from paperhub.agents.core.note_tools import register_note_tools
    from paperhub.agents.core.tools import ToolRegistry
    from paperhub.llm.adapter import ToolCall

    reg = ToolRegistry()
    register_note_tools(reg)
    out = await reg.run(ctx, ToolCall(id="c", name=name, arguments=args))
    assert not out.record.get("error"), out.content
    return out


async def _ids(conn: aiosqlite.Connection, note_id: int) -> list[str]:
    async with conn.execute(
        "SELECT id FROM note_blocks WHERE note_id = ? ORDER BY order_key", (note_id,),
    ) as cur:
        return [str(r[0]) for r in await cur.fetchall()]


async def _user_update(conn: aiosqlite.Connection, note_id: int, bid: str) -> None:
    await apply_ops(conn, note_id, base_revision=await _rev(conn, note_id),
                    actor="user", ops=[Op(op="update", block_id=bid, block=BlockIn(
                        type="paragraph", content="edited by the user"))])


async def _changes(conn: aiosqlite.Connection) -> str:
    state = await build_session_state(conn, 1, {})
    block = re.search(r"<notes_changes>.*?</notes_changes>", state, re.S)
    return block.group(0) if block else ""


async def test_search_hit_does_not_hide_an_unseen_block_change(
    migrated_db: aiosqlite.Connection, fake_tracer: Any,
) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "alpha topic", "user")
    await _write(migrated_db, nid, "beta topic", "user")
    a, b = await _ids(migrated_db, nid)
    ctx = _tool_ctx(migrated_db, fake_tracer)
    await _tool(ctx, "search_notes", query="alpha")  # first contact: sees A only
    await _user_update(migrated_db, nid, b)
    assert b in await _changes(migrated_db)
    await _tool(ctx, "search_notes", query="alpha")  # sees A again, never B
    changes = await _changes(migrated_db)
    assert b in changes and a not in changes


async def test_unread_page_change_is_reported(
    migrated_db: aiosqlite.Connection, fake_tracer: Any,
) -> None:
    nid = await _note(migrated_db, 1)
    await apply_ops(migrated_db, nid, base_revision=0, actor="user",
                    ops=[_p(f"para {i}") for i in range(45)])
    ids = await _ids(migrated_db, nid)
    ctx = _tool_ctx(migrated_db, fake_tracer)
    await _tool(ctx, "read_note", note="chat")  # page 1: blocks 0-39
    await _user_update(migrated_db, nid, ids[42])  # on page 2
    assert ids[42] in await _changes(migrated_db)
    await _user_update(migrated_db, nid, ids[3])  # seen on page 1, changed since
    assert ids[3] in await _changes(migrated_db)
    await _tool(ctx, "read_note", note="chat", page=2)
    changes = await _changes(migrated_db)
    assert ids[42] not in changes and ids[3] in changes


async def test_agent_write_not_reported_but_user_edit_of_it_is(
    migrated_db: aiosqlite.Connection, fake_tracer: Any,
) -> None:
    nid = await _note(migrated_db, 1)
    await _write(migrated_db, nid, "user's line", "user")
    (first,) = await _ids(migrated_db, nid)
    ctx = _tool_ctx(migrated_db, fake_tracer)
    out = await _tool(ctx, "edit_note", note="chat",
                      base_revision=await _rev(migrated_db, nid),
                      ops=[{"op": "insert", "after": first, "block": {
                          "type": "paragraph", "content": "agent's line"}}])
    (mine,) = json.loads(out.content)["applied"]
    assert await _changes(migrated_db) == ""
    await _user_update(migrated_db, nid, mine)
    assert mine in await _changes(migrated_db)
