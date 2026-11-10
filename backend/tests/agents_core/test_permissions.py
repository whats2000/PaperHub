"""Consent gate: pending confirmations, auto-approve classifier (H4 Task 8)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.permissions import ApproveDecision
from paperhub.agents.core.tools import (
    Tier,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
)
from paperhub.llm.adapter import ToolCall
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


class _Adapter:
    def __init__(self, result: ApproveDecision | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def structured(self, **kw: Any) -> ApproveDecision:
        self.calls.append(kw)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _setup(
    conn: aiosqlite.Connection, tracer: Tracer, adapter: Any,
) -> tuple[ToolContext, ToolRegistry, list[dict[str, Any]]]:
    ran: list[dict[str, Any]] = []

    async def handler(_ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        ran.append(args)
        return ToolOutcome(content="done", record={"ok": True})

    params = {"type": "object", "properties": {}, "required": []}
    reg = ToolRegistry()
    reg.register(ToolSpec("danger", "d", params, Tier.CONFIRM, handler))
    reg.register(ToolSpec("safe", "s", params, Tier.FREE, handler))

    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    ctx = ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=tracer.run_id,
        settings=SimpleNamespace(agent_autoapprove_model=""),  # type: ignore[arg-type]
        adapter=adapter, emit=emit,
        enabled_papers=[], history=[], state={"user_message": "remove it"},
    )
    return ctx, reg, ran


async def _rows(conn: aiosqlite.Connection, sql: str) -> list[tuple[Any, ...]]:
    async with conn.execute(sql) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def _auto_on(conn: aiosqlite.Connection) -> None:
    await conn.execute("UPDATE chat_sessions SET auto_approve = 1")
    await conn.commit()


async def test_confirm_tool_is_pending_when_auto_approve_off(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    adapter = _Adapter(ApproveDecision(run=True, reason="x"))
    ctx, reg, ran = _setup(migrated_db, fake_tracer, adapter)
    out = await reg.run(ctx, ToolCall(id="c", name="danger", arguments={"k": 1}))
    assert ran == [] and adapter.calls == []
    assert "do not retry" in out.content
    name, payload = out.events[0]
    assert name == "confirm_request"
    assert payload["tool"] == "danger" and payload["action_id"]
    rows = await _rows(
        migrated_db, "SELECT id, tool, status, args_json FROM pending_actions")
    assert rows == [(payload["action_id"], "danger", "pending", json.dumps({"k": 1}))]
    assert out.record["pending_action_id"] == payload["action_id"]
    traced = await _rows(
        migrated_db,
        "SELECT result_summary_json FROM tool_calls WHERE tool='agent:approve'")
    assert json.loads(traced[0][0])["decision"] == "ask"
    assert json.loads(traced[0][0])["pending_id"] == payload["action_id"]


async def test_free_tool_never_gated(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    ctx, reg, ran = _setup(migrated_db, fake_tracer, _Adapter(Exception("no")))
    await reg.run(ctx, ToolCall(id="c", name="safe", arguments={}))
    assert len(ran) == 1
    assert await _rows(migrated_db, "SELECT * FROM pending_actions") == []


async def test_auto_approve_run_true_runs_and_traces(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    await _auto_on(migrated_db)
    adapter = _Adapter(ApproveDecision(run=True, reason="user asked"))
    ctx, reg, ran = _setup(migrated_db, fake_tracer, adapter)
    out = await reg.run(ctx, ToolCall(id="c", name="danger", arguments={"k": 1}))
    assert ran == [{"k": 1}] and out.content == "done"
    assert adapter.calls[0]["slot"] == "agent_autoapprove/v2"
    assert adapter.calls[0]["variables"]["user_message"] == "remove it"
    assert await _rows(migrated_db, "SELECT * FROM pending_actions") == []
    traced = await _rows(
        migrated_db,
        "SELECT result_summary_json FROM tool_calls WHERE tool='agent:approve'")
    rec = json.loads(traced[0][0])
    assert rec["decision"] == "run" and rec["auto_approve"] is True
    assert rec["reason"] == "user asked"


async def test_auto_approve_run_false_asks(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    await _auto_on(migrated_db)
    adapter = _Adapter(ApproveDecision(run=False, reason="vague"))
    ctx, reg, ran = _setup(migrated_db, fake_tracer, adapter)
    out = await reg.run(ctx, ToolCall(id="c", name="danger", arguments={}))
    assert ran == [] and out.events[0][0] == "confirm_request"


async def test_classifier_failure_fails_closed(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    await _auto_on(migrated_db)
    ctx, reg, ran = _setup(migrated_db, fake_tracer, _Adapter(RuntimeError("boom")))
    out = await reg.run(ctx, ToolCall(id="c", name="danger", arguments={}))
    assert ran == [] and out.events[0][0] == "confirm_request"
    assert "classifier failed" in out.events[0][1]["reason"]


async def test_execute_confirmed_skips_gate_and_traces(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    ctx, reg, ran = _setup(migrated_db, fake_tracer, _Adapter(Exception("no")))
    out = await reg.execute_confirmed(ctx, "act1", "danger", {"k": 2})
    assert ran == [{"k": 2}] and out.content == "done"
    traced = await _rows(
        migrated_db,
        "SELECT args_redacted_json FROM tool_calls WHERE tool='tool:danger'")
    assert json.loads(traced[0][0])["confirmed_action_id"] == "act1"


# ------------------------------------------------- review #1 / #4 / #12


def _real_ctx(
    conn: aiosqlite.Connection, tracer: Tracer, adapter: Any, message: str,
    enabled: list[tuple[int, str]] | None = None,
) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=tracer.run_id,
        settings=SimpleNamespace(agent_autoapprove_model=""),  # type: ignore[arg-type]
        adapter=adapter, emit=emit, enabled_papers=enabled or [], history=[],
        state={"user_message": message},
    )


async def _memory(conn: aiosqlite.Connection, mid: int, scope: str,
                  content: str, session_id: int | None = None) -> None:
    await conn.execute(
        "INSERT INTO memories (id, scope, session_id, content) VALUES (?, ?, ?, ?)",
        (mid, scope, session_id, content))
    await conn.commit()


async def test_forget_card_and_classifier_see_the_memory_text(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.agents.core.action_tools import build_default_registry

    await _memory(migrated_db, 3, "global", "always answer in <Japanese>")
    await _auto_on(migrated_db)
    adapter = _Adapter(ApproveDecision(run=False, reason="unsure"))
    ctx = _real_ctx(migrated_db, fake_tracer, adapter, "forget <that> & stuff")
    reg = build_default_registry()
    out = await reg.run(ctx, ToolCall(id="c", name="forget", arguments={"memory_id": 3}))
    name, payload = out.events[0]
    assert name == "confirm_request"
    # The eval corpus's format, which the classifier was evaluated on.
    assert payload["args_summary"] == 'memory 3: "always answer in <Japanese>"'
    assert payload["target"] == {"kind": "memory", "id": 3,
                                 "content": "always answer in <Japanese>",
                                 "scope": "global"}
    assert payload["reason_kind"] == "classifier"
    variables = adapter.calls[0]["variables"]
    assert variables["args_summary"] == 'memory 3: "always answer in &lt;Japanese&gt;"'
    assert variables["user_message"] == "forget &lt;that&gt; &amp; stuff"


async def test_remove_paper_summary_matches_the_eval_format(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.agents.core.action_tools import build_default_registry

    ctx = _real_ctx(migrated_db, fake_tracer, _Adapter(Exception("x")),
                    "drop it", enabled=[(4, "Segment Anything")])
    out = await build_default_registry().run(
        ctx, ToolCall(id="c", name="remove_paper", arguments={"paper": 4}))
    _, payload = out.events[0]
    assert payload["args_summary"] == '"Segment Anything" (paper 4)'
    assert payload["target"] == {"kind": "paper", "id": 4, "title": "Segment Anything"}
    assert payload["reason_kind"] == "required"


async def test_forget_guessed_id_fails_cleanly_without_a_card(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.agents.core.action_tools import build_default_registry

    await _memory(migrated_db, 5, "global", "lives in Taipei")
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await _memory(migrated_db, 6, "session", "other chat's note", session_id=2)
    ctx = _real_ctx(migrated_db, fake_tracer, _Adapter(Exception("x")), "forget it")
    reg = build_default_registry()
    for bad in (99, 6):
        out = await reg.run(ctx, ToolCall(id="c", name="forget",
                                          arguments={"memory_id": bad}))
        assert out.record.get("error") and out.events == []
        assert "5" in out.content and "lives in Taipei" in out.content
    assert await _rows(migrated_db, "SELECT * FROM pending_actions") == []


async def test_confirmed_forget_reports_the_memory_and_refuses_hidden_ids(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.agents.core.action_tools import build_default_registry

    await _memory(migrated_db, 7, "session", "NTU student", session_id=1)
    ctx = _real_ctx(migrated_db, fake_tracer, None, "")
    reg = build_default_registry()
    out = await reg.execute_confirmed(ctx, "a", "forget", {"memory_id": 7})
    assert out.record["content"] == "NTU student" and out.record["scope"] == "session"
    gone = await reg.execute_confirmed(ctx, "b", "forget", {"memory_id": 7})
    assert gone.record.get("error")
