"""Ask first governs the agent's search call (SRS §III-10.5, H5a wave 3).

In Ask first mode a live turn's ``search_papers`` call pauses on an
"Allow this search?" card before anything leaves; Auto mode, a chat whose
searches were allowed, and callers outside a live turn run it at once.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from paperhub import approvals
from paperhub.agents.core.action_tools import render_action_content
from paperhub.agents.core.tools import Tier, ToolContext, ToolOutcome, ToolRegistry, ToolSpec
from paperhub.approvals import ApprovalDecision
from paperhub.llm.adapter import ToolCall
from paperhub.tracing.tracer import Tracer

pytestmark = [pytest.mark.asyncio, pytest.mark.ask_first]


def _setup(
    conn: aiosqlite.Connection, tracer: Tracer,
) -> tuple[ToolContext, ToolRegistry, list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    ran: list[dict[str, Any]] = []
    shown: list[tuple[str, dict[str, Any]]] = []

    async def handler(_ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        ran.append(args)
        return ToolOutcome(content="found", record={
            "query": args["query"], "mode": "search", "candidates": [],
            "held": [], "blocked": []})

    params = {"type": "object", "properties": {"query": {"type": "string"}},
              "required": ["query"]}
    reg = ToolRegistry()
    reg.register(ToolSpec("search_papers", "s", params, Tier.FREE, handler))

    async def emit(n: str, p: dict[str, Any]) -> None:
        shown.append((n, p))

    ctx = ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=tracer.run_id,
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        adapter=None,  # type: ignore[arg-type]
        emit=emit, enabled_papers=[], history=[], state={"user_message": "find"},
        mcp_registry=object(),
    )
    return ctx, reg, ran, shown


async def _rows(conn: aiosqlite.Connection, sql: str) -> list[tuple[Any, ...]]:
    async with conn.execute(sql) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def _paused(
    conn: aiosqlite.Connection, tracer: Tracer,
) -> tuple[asyncio.Task[ToolOutcome], list[dict[str, Any]], list[tuple[str, dict[str, Any]]],
           str]:
    stream: list[tuple[str, dict[str, Any]]] = []
    approvals.open_run(tracer.run_id, lambda n, d: stream.append((n, d)))
    ctx, reg, ran, shown = _setup(conn, tracer)
    task = asyncio.create_task(reg.run(ctx, ToolCall(
        id="c1", name="search_papers", arguments={"query": "sparse priors"})))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if shown:
            break
    assert shown and shown[0][0] == "confirm_request"
    card = shown[0][1]
    assert card["tool"] == "search_papers"
    assert card["reason_kind"] == "ask_first"
    assert card["target"]["kind"] == "search"
    assert card["target"]["query"] == "sparse priors"
    assert "api.semanticscholar.org" in card["target"]["destinations"]
    await asyncio.sleep(0.01)
    assert not task.done(), "nothing may leave before the user allows it"
    assert ran == []
    action_id = str(card["action_id"])
    assert stream[-1] == ("paused", {"run_id": tracer.run_id, "cards": [
        {"kind": "search", "id": action_id, "tool": "search_papers",
         "call_id": "c1"}]})
    rows = await _rows(conn, "SELECT tool, status FROM pending_actions")
    assert rows == [("search_papers", "pending")]
    return task, ran, stream, action_id


async def _wait_record(conn: aiosqlite.Connection) -> dict[str, Any]:
    rows = await _rows(conn, "SELECT result_summary_json FROM tool_calls "
                             "WHERE tool = 'agent:await_approval'")
    return dict(json.loads(rows[0][0]))


async def test_allow_runs_the_search_in_the_run(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    try:
        task, ran, stream, aid = await _paused(migrated_db, fake_tracer)
        assert approvals.deliver(f"search:{aid}", ApprovalDecision(True)) is not None
        out = await task
        assert ran == [{"query": "sparse priors"}]
        # The model reads the result as rendered from the record (export parity).
        assert out.content == render_action_content("search_papers", out.record)
        assert out.record["ask"]["decision"] == "allowed"
        assert stream[-1][0] == "resumed"
        rows = await _rows(migrated_db, "SELECT status, result_json FROM pending_actions")
        assert rows[0][0] == "done"
        assert json.loads(rows[0][1])["text"] == "sparse priors"
        rec = await _wait_record(migrated_db)
        assert rec == {**rec, "kind": "search", "decision": "approved",
                       "edited": False, "note_given": False, "allow_chat": False}
    finally:
        approvals.close_run(fake_tracer.run_id)


async def test_edit_then_allow_sends_the_edited_query(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    try:
        task, ran, _s, aid = await _paused(migrated_db, fake_tracer)
        approvals.deliver(f"search:{aid}", ApprovalDecision(
            True, "sparsity segmentation", edited=True, allow_chat=True))
        out = await task
        assert ran == [{"query": "sparsity segmentation"}]
        assert out.record["ask"]["edited"] is True
        # The model is told its query was changed.
        assert "sparsity segmentation" in out.content
        # The tool row keeps the model's own call (feedback export matches it).
        rows = await _rows(migrated_db, "SELECT args_redacted_json FROM tool_calls "
                                        "WHERE tool = 'tool:search_papers'")
        assert json.loads(rows[0][0])["query"] == "sparse priors"
        rec = await _wait_record(migrated_db)
        assert rec["edited"] is True and rec["allow_chat"] is True
    finally:
        approvals.close_run(fake_tracer.run_id)


async def test_dont_allow_passes_the_note_and_runs_nothing(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    try:
        task, ran, _s, aid = await _paused(migrated_db, fake_tracer)
        approvals.deliver(f"search:{aid}", ApprovalDecision(
            False, note="Only use papers already in this chat."))
        out = await task
        assert ran == []
        assert out.record["declined"] is True
        assert out.record["note"] == "Only use papers already in this chat."
        assert "Only use papers already in this chat." in out.content
        assert "not" in out.content.lower()
        rec = await _wait_record(migrated_db)
        assert rec["decision"] == "declined" and rec["note_given"] is True
    finally:
        approvals.close_run(fake_tracer.run_id)


async def test_dont_allow_without_a_note(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    try:
        task, ran, _s, aid = await _paused(migrated_db, fake_tracer)
        approvals.deliver(f"search:{aid}", ApprovalDecision(False))
        out = await task
        assert ran == [] and out.record["note"] is None
        assert "<user_note>" not in out.content
    finally:
        approvals.close_run(fake_tracer.run_id)


async def test_stop_ends_the_wait_and_sends_nothing(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    try:
        task, ran, _s, _aid = await _paused(migrated_db, fake_tracer)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ran == []
        rec = await _wait_record(migrated_db)
        assert rec["kind"] == "search" and rec["decision"] == "stopped"
    finally:
        approvals.close_run(fake_tracer.run_id)


@pytest.mark.parametrize("column", ["auto_approve", "allow_searches"])
async def test_auto_mode_or_allowed_chat_runs_without_a_card(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, column: str,
) -> None:
    await migrated_db.execute(f"UPDATE chat_sessions SET {column} = 1")
    await migrated_db.commit()
    approvals.open_run(fake_tracer.run_id)
    try:
        ctx, reg, ran, shown = _setup(migrated_db, fake_tracer)
        await reg.run(ctx, ToolCall(id="c", name="search_papers",
                                    arguments={"query": "q"}))
        assert ran == [{"query": "q"}] and shown == []
        assert await _rows(migrated_db, "SELECT id FROM pending_actions") == []
    finally:
        approvals.close_run(fake_tracer.run_id)


async def test_outside_a_live_turn_nothing_asks(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    ctx, reg, ran, shown = _setup(migrated_db, fake_tracer)
    await reg.run(ctx, ToolCall(id="c", name="search_papers", arguments={"query": "q"}))
    assert ran == [{"query": "q"}] and shown == []


async def test_an_approved_held_follow_up_does_not_ask_again(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    approvals.open_run(fake_tracer.run_id)
    try:
        ctx, reg, ran, shown = _setup(migrated_db, fake_tracer)
        ctx.state["approved_held_id"] = "h1"
        await reg.run(ctx, ToolCall(id="c", name="search_papers",
                                    arguments={"query": "q"}))
        assert ran == [{"query": "q"}] and shown == []
    finally:
        approvals.close_run(fake_tracer.run_id)
