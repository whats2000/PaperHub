"""Nothing is created by looking, on the agent's side too (H5b.1 Task 14):
the ``<notes>`` index, ``list_notes``, ``search_notes`` and ``read_note``
read a note that does not exist yet as an empty one (revision 0, no
blocks, the default title) and create nothing; only ``edit_note`` does."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.core.context import render_notes
from paperhub.agents.core.note_tools import register_note_tools
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.notes.papers import paper_note
from paperhub.notes.store import get_or_create_note
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _seed(conn: aiosqlite.Connection) -> int:
    """Chat 1 (made by ``fake_tracer``) with an attached paper and an idea."""
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES (3, 'arxiv:3', 'arxiv', '3', 'A Paper', 's', 'd', 'h')")
    await conn.execute("INSERT INTO papers (session_id, paper_content_id) VALUES (1, 3)")
    await conn.execute("UPDATE chat_sessions SET title = 'My chat' WHERE id = 1")
    await conn.execute(
        "INSERT INTO ideas (id, source_doc, source_section, title) "
        "VALUES (5, 3, '1 Intro', 'An idea')")
    await conn.execute("INSERT INTO idea_sessions (idea_id, session_id) VALUES (5, 1)")
    await conn.commit()
    return 3


async def _note_rows(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT COUNT(*) FROM notes") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


def _ctx(conn: aiosqlite.Connection, tracer: Tracer) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=[(3, "A Paper")],
        history=[], state={})


async def _run(ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    reg = ToolRegistry()
    register_note_tools(reg)
    return await reg.run(ctx, ToolCall(id="c1", name=name, arguments=args))


async def test_reads_create_nothing_and_read_as_before(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    conn = migrated_db
    await _seed(conn)
    ctx = _ctx(conn, fake_tracer)

    index, changes = await render_notes(conn, 1, [3])
    listed = json.loads((await _run(ctx, "list_notes")).content)
    read_chat = await _run(ctx, "read_note", note="chat")
    read_paper = await _run(ctx, "read_note", note="paper:3")
    read_idea = await _run(ctx, "read_note", note="idea:5")
    searched = await _run(ctx, "search_notes", query="anything")
    assert await _note_rows(conn) == 0
    async with conn.execute("SELECT COUNT(*) FROM note_agent_reads") as cur:
        assert (await cur.fetchone())[0] == 0  # type: ignore[index]

    # What the model sees is what it saw when these reads created the
    # notes (an empty note: revision 0, no blocks, the default title).
    await get_or_create_note(conn, target_kind="session", target_id="1",
                             title="", session_id=1)
    await paper_note(conn, 3)
    await get_or_create_note(conn, target_kind="idea", target_id="5", title="")
    assert (index, changes) == await render_notes(conn, 1, [3])
    assert index is not None and 'ref="chat"' in index and 'ref="paper:3"' in index
    after = json.loads((await _run(ctx, "list_notes")).content)
    assert [{k: v for k, v in r.items() if k != "target"} for r in listed["notes"]] == [
        {k: v for k, v in r.items() if k != "target"} for r in after["notes"]]
    assert read_chat.content == (await _run(ctx, "read_note", note="chat")).content
    assert read_paper.content == (await _run(ctx, "read_note", note="paper:3")).content
    assert read_idea.content == (await _run(ctx, "read_note", note="idea:5")).content
    assert searched.content == (await _run(ctx, "search_notes", query="anything")).content


async def test_edit_note_is_what_creates(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    conn = migrated_db
    await _seed(conn)
    ctx = _ctx(conn, fake_tracer)
    out = await _run(ctx, "edit_note", note="paper:3", base_revision=0, ops=[
        {"op": "insert", "block": {"type": "paragraph",
                                   "content": [{"type": "text", "text": "hi"}]}}])
    assert not out.record.get("error"), out.content
    assert await _note_rows(conn) == 1
