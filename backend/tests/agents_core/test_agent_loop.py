"""Tests for the core agent loop (H4 Task 9)."""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.agent import (
    AGENT_MAX_STEPS,
    AgentDeps,
    AgentEvent,
    Final,
    Step,
    Token,
    ToolEvent,
    run_core_agent,
)
from paperhub.agents.core.read_tools import register_read_tools
from paperhub.agents.core.tools import (
    Tier,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
)
from paperhub.llm.adapter import ToolCall, ToolTurn
from paperhub.tracing.tracer import Tracer

from .test_read_tools import _paper

pytestmark = pytest.mark.asyncio

Script = Callable[[dict[str, Any]], ToolTurn]


class FakeAdapter:
    """Scripted ``tool_turn``: each call pops the next turn (or asks a
    function). Content is streamed through ``on_token`` in two pieces."""

    def __init__(self, turns: list[ToolTurn] | Script) -> None:
        self._turns = turns
        self.calls: list[dict[str, Any]] = []

    async def tool_turn(self, **kw: Any) -> ToolTurn:
        self.calls.append(kw)
        turn = (self._turns(kw) if callable(self._turns)
                else self._turns.pop(0))
        on_token: Callable[[str], Awaitable[None]] | None = kw.get("on_token")
        if on_token is not None and turn.content:
            half = len(turn.content) // 2
            await on_token(turn.content[:half])
            await on_token(turn.content[half:])
        return turn


def _calls(*calls: tuple[str, dict[str, Any]]) -> ToolTurn:
    return ToolTurn(content="", tool_calls=[
        ToolCall(id=f"call_{i}__thought__sig==", name=n, arguments=a)
        for i, (n, a) in enumerate(calls)
    ])


def _answer(text: str) -> ToolTurn:
    return ToolTurn(content=text)


async def _setup(conn: aiosqlite.Connection) -> tuple[int, int, Tracer]:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
    await conn.commit()
    return 1, 1, Tracer(conn, run_id=1, branch="")


def _settings() -> Any:
    return SimpleNamespace(agent_model="flagship-test", agent_max_steps=12, agent_max_turn_tokens=120000, agent_keep_turns=12)


async def _drive(
    conn: aiosqlite.Connection, tracer: Tracer, adapter: FakeAdapter,
    registry: ToolRegistry, message: str = "question",
    history: list[dict[str, Any]] | None = None,
) -> list[AgentEvent]:
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=conn,  # type: ignore[arg-type]
                     settings=_settings(), registry=registry)
    state: dict[str, Any] = {"run_id": 1, "session_id": 1,
                             "user_message": message, "history": history or []}
    return [e async for e in run_core_agent(state, deps)]  # type: ignore[arg-type]


async def _rows(conn: aiosqlite.Connection, tool: str) -> list[dict[str, Any]]:
    async with conn.execute(
        "SELECT step_index, args_redacted_json, result_summary_json, status "
        "FROM tool_calls WHERE run_id = 1 AND tool = ? ORDER BY step_index",
        (tool,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"step_index": r[0], "args": json.loads(r[1] or "null"),
             "result": json.loads(r[2] or "null"), "status": r[3]} for r in rows]


async def _enable(conn: aiosqlite.Connection, pid: int) -> None:
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id) VALUES (1, ?)", (pid,))
    await conn.commit()


def _read_registry() -> ToolRegistry:
    r = ToolRegistry()
    register_read_tools(r)
    return r


async def test_follow_up_carries_history_into_model_messages(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review Focus 1, end to end: the loop sends the earlier answer and its
    work record to the model."""
    sid, _, tracer = await _setup(migrated_db)
    answer = "Port GISC sparsity into YOLOSeg's loss [chunk:11]."
    await migrated_db.execute(
        "INSERT INTO runs (session_id, agent_record_json) VALUES (?, ?)",
        (sid, json.dumps({"tools": ["gather"], "read": [11], "cited": [11],
                          "papers": [], "steps": 2})),
    )
    await migrated_db.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (1, 'assistant', ?, 2)", (answer,))
    await migrated_db.commit()
    adapter = FakeAdapter([_answer("More detail on that port [chunk:11].")])
    events = await _drive(
        migrated_db, tracer, adapter, _read_registry(), "給我更詳細實驗方法",
        history=[{"role": "user", "content": "GISC to YOLOSeg?"},
                 {"role": "assistant", "content": answer}],
    )
    sent = adapter.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    joined = "\n".join(str(m.get("content")) for m in sent)
    assert answer in joined and "<work_record>" in joined
    assert sent[-1] == {"role": "user", "content": "給我更詳細實驗方法"}
    final = events[-1]
    assert isinstance(final, Final)
    # chunk 11 is citable from the kept work record without a re-read
    assert final.text == "More detail on that port [chunk:11]."


async def test_parallel_tool_calls_run_concurrently_and_trace_each(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    started: list[str] = []
    both = asyncio.Event()

    async def slow(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        started.append(args["tag"])
        if len(started) == 2:
            both.set()
        # Sequential execution would deadlock here: the first call waits for
        # the second to start.
        await asyncio.wait_for(both.wait(), timeout=2)
        return ToolOutcome(content=f"done {args['tag']}", record={"tag": args["tag"]},
                           events=[("search_results", {"candidates": [args["tag"]]})])

    reg = ToolRegistry()
    reg.register(ToolSpec("slow", "slow tool",
                          {"type": "object", "properties": {"tag": {"type": "string"}},
                           "required": ["tag"]}, Tier.FREE, slow))
    adapter = FakeAdapter([_calls(("slow", {"tag": "a"}), ("slow", {"tag": "b"})),
                           _answer("ok")])
    events = await _drive(migrated_db, tracer, adapter, reg)
    assert sorted(started) == ["a", "b"]
    rows = await _rows(migrated_db, "tool:slow")
    assert sorted(r["result"]["tag"] for r in rows) == ["a", "b"]
    assert {r["args"]["call_id"] for r in rows} == {
        "call_0__thought__sig==", "call_1__thought__sig=="}
    # the tool messages echo the verbatim ids, in call order
    second = adapter.calls[1]["messages"]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == [
        "call_0__thought__sig==", "call_1__thought__sig=="]
    assert [m["content"] for m in tool_msgs] == ["done a", "done b"]
    asst = next(m for m in second if m["role"] == "assistant")
    assert [c["id"] for c in asst["tool_calls"]] == [
        "call_0__thought__sig==", "call_1__thought__sig=="]
    tool_events = [e for e in events
                   if isinstance(e, ToolEvent) and e.name != "tool_step"]
    assert [e.payload["candidates"] for e in tool_events] == [["a"], ["b"]]
    steps = await _rows(migrated_db, "agent:step")
    assert len(steps) == 2
    assert [c["name"] for c in steps[0]["result"]["tool_calls"]] == ["slow", "slow"]
    assert [r["status"] for r in steps[0]["result"]["tool_results"]] == ["ok", "ok"]
    assert steps[1]["result"]["answer"] == "ok"
    assert sum(isinstance(e, Step) for e in events) == 2


async def test_tool_step_events_forwarded_once(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def piped(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        rec = {"step_index": 7, "tool": "paper_search:parse"}
        return ToolOutcome(content="x", record={},
                           events=[("tool_step", {"record": rec}),
                                   ("tool_step", {"record": rec})])

    reg = ToolRegistry()
    reg.register(ToolSpec("piped", "p", {"type": "object", "properties": {}},
                          Tier.FREE, piped))
    adapter = FakeAdapter([_calls(("piped", {})), _calls(("piped", {})),
                           _answer("done")])
    events = await _drive(migrated_db, tracer, adapter, reg)
    steps = [e for e in events if isinstance(e, ToolEvent) and e.name == "tool_step"
             and e.payload["record"]["step_index"] >= 0]
    assert len(steps) == 1


async def test_budget_reached_forces_an_answer(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review Focus 2: a model that never stops calling tools still answers."""
    _, _, tracer = await _setup(migrated_db)

    def script(kw: dict[str, Any]) -> ToolTurn:
        if kw.get("tool_choice") == "none":
            return _answer("I compared the losses but did not get to the "
                           "training schedule.")
        return _calls(("noop", {}))

    async def noop(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="nothing", record={})

    reg = ToolRegistry()
    reg.register(ToolSpec("noop", "n", {"type": "object", "properties": {}},
                          Tier.FREE, noop))
    adapter = FakeAdapter(script)
    events = await _drive(migrated_db, tracer, adapter, reg)
    assert len(adapter.calls) == AGENT_MAX_STEPS + 1
    forced = adapter.calls[-1]
    assert forced["tool_choice"] == "none"
    assert "budget" in forced["messages"][-1]["content"].lower()
    final = events[-1]
    assert isinstance(final, Final)
    assert "did not get to" in final.text
    assert final.record["budget_reached"] is True
    steps = await _rows(migrated_db, "agent:step")
    assert len(steps) == AGENT_MAX_STEPS + 1
    assert all(s["status"] == "ok" for s in steps)
    assert steps[-1]["result"]["budget_reached"] is True


async def test_token_budget_forces_an_answer(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def big(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="word " * 200, record={})

    reg = ToolRegistry()
    reg.register(ToolSpec("big", "b", {"type": "object", "properties": {}},
                          Tier.FREE, big))

    def script(kw: dict[str, Any]) -> ToolTurn:
        if kw.get("tool_choice") == "none":
            return _answer("partial")
        return _calls(("big", {}))

    adapter = FakeAdapter(script)
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=SimpleNamespace(agent_model="m", agent_max_steps=12, agent_max_turn_tokens=300, agent_keep_turns=12),
                     registry=reg)
    events = [e async for e in run_core_agent(
        {"run_id": 1, "session_id": 1, "user_message": "q", "history": []}, deps)]
    # 200 words exceed nothing; 400 exceed 300 -> forced after 2 tool steps
    assert len(adapter.calls) == 3
    assert isinstance(events[-1], Final) and events[-1].text == "partial"


async def test_invented_citation_removed_and_traced(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review Focus 3: only chunks read this turn (or kept records) survive."""
    _, _, tracer = await _setup(migrated_db)
    pid, ids = await _paper(migrated_db, "2401.0001", {"Loss": ["L = a + b."]})
    await _enable(migrated_db, pid)
    read = ids["L = a + b."]
    adapter = FakeAdapter([
        _calls(("read_chunks", {"ids": [read]})),
        _answer(f"The loss is L [chunk:{read}], tuned by AdamW [chunk:99999]."),
    ])
    events = await _drive(migrated_db, tracer, adapter, _read_registry())
    final = events[-1]
    assert isinstance(final, Final)
    assert final.text == f"The loss is L [chunk:{read}], tuned by AdamW [unsourced:not_read]."
    # R7: tokens stream the raw answer live; Final carries the validated text
    tokens = "".join(e.text for e in events if isinstance(e, Token))
    assert tokens == f"The loss is L [chunk:{read}], tuned by AdamW [chunk:99999]."
    steps = await _rows(migrated_db, "agent:step")
    assert steps[-1]["result"]["citations_removed"] == [99999]
    assert steps[0]["result"]["tool_results"][0]["ids"] == [read]
    async with migrated_db.execute(
        "SELECT agent_record_json FROM runs WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row is not None
    record = json.loads(row[0])
    assert record == {"tools": ["read_chunks"], "read": [read], "cited": [read],
                      "papers": [pid], "steps": 2}


async def test_no_papers_answer_offers_search(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review Focus 5: with no enabled papers the read tool returns a clear
    empty result without touching chunk data, and the answer offers search."""
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([
        _calls(("read_section", {"paper": 1, "section": "Method"})),
        _answer("No papers are enabled in this chat. I can search_papers for "
                "it if you like."),
    ])
    events = await _drive(migrated_db, tracer, adapter, _read_registry())
    # the session state told the model there are no papers
    first = adapter.calls[0]["messages"]
    assert any("(none)" in str(m.get("content")) for m in first)
    tool_msg = next(m for m in adapter.calls[1]["messages"] if m["role"] == "tool")
    assert "No papers are enabled" in tool_msg["content"]
    rows = await _rows(migrated_db, "tool:read_section")
    assert rows[0]["status"] == "error"
    assert "chunk_ids" not in rows[0]["result"]
    final = events[-1]
    assert isinstance(final, Final) and "search_papers" in final.text
    steps = await _rows(migrated_db, "agent:step")
    assert steps[0]["result"]["tool_results"][0]["status"] == "error"


async def test_context_token_counts_recorded(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([_answer("hello")])
    events = await _drive(migrated_db, tracer, adapter, _read_registry())
    steps = await _rows(migrated_db, "agent:step")
    counts = steps[0]["result"]["context_tokens"]
    assert set(counts) == {"instructions", "tools", "session_state",
                           "conversation", "this_turn"}
    assert counts["instructions"] > 0 and counts["tools"] > 0
    assert steps[0]["result"]["model"] == "flagship-test"
    assert steps[0]["result"]["step"] == 1
    assert [type(e).__name__ for e in events] == ["Token", "Token", "Step", "Final"]


async def test_confirm_tool_pending_is_not_retried_and_proposals_parsed(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def remove(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        raise AssertionError("must not run without confirmation")

    reg = ToolRegistry()
    reg.register(ToolSpec("drop_thing", "r",
                          {"type": "object", "properties": {"paper": {"type": "integer"}},
                           "required": ["paper"]}, Tier.CONFIRM, remove))
    adapter = FakeAdapter([
        _calls(("drop_thing", {"paper": 3})),
        _answer("Waiting for your confirmation.\n<proposal>Make slides on it</proposal>"),
    ])
    events = await _drive(migrated_db, tracer, adapter, reg)
    confirm = [e for e in events if isinstance(e, ToolEvent)
               and e.name == "confirm_request"]
    assert len(confirm) == 1
    final = events[-1]
    assert isinstance(final, Final)
    assert final.record["proposals"] == ["Make slides on it"]
    assert "<proposal>Make slides on it</proposal>" in final.text
    steps = await _rows(migrated_db, "agent:step")
    assert steps[0]["result"]["tool_results"][0]["status"] == "pending"


async def test_dropped_calls_recorded_on_step(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([ToolTurn(content="hi", dropped_calls=[{"id": "x"}])])
    await _drive(migrated_db, tracer, adapter, _read_registry())
    steps = await _rows(migrated_db, "agent:step")
    assert steps[0]["result"]["dropped_calls"] == [{"id": "x"}]


async def test_state_and_mcp_registry_reach_tools(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    seen: dict[str, Any] = {}

    async def probe(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        seen["state"] = dict(ctx.state)
        seen["mcp"] = ctx.mcp_registry
        return ToolOutcome(content="ok", record={})

    reg = ToolRegistry()
    reg.register(ToolSpec("probe", "p", {"type": "object", "properties": {}},
                          Tier.FREE, probe))
    adapter = FakeAdapter([_calls(("probe", {})), _answer("done")])
    marker = object()
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=_settings(), registry=reg, mcp_registry=marker)
    _ = [e async for e in run_core_agent(
        {"run_id": 1, "session_id": 1, "user_message": "hi there",
         "history": []}, deps)]
    assert seen["mcp"] is marker
    assert seen["state"]["run_id"] == 1
    assert seen["state"]["session_id"] == 1
    assert seen["state"]["user_message"] == "hi there"


async def test_cancel_mid_tool_cancels_the_tool_task(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Stop pressed during a slow tool: the in-flight tool task is cancelled
    with the loop and the agent:step row records the cancellation."""
    _, _, tracer = await _setup(migrated_db)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ToolOutcome(content="never", record={})

    reg = ToolRegistry()
    reg.register(ToolSpec("hang", "h", {"type": "object", "properties": {}},
                          Tier.FREE, hang))
    adapter = FakeAdapter([_calls(("hang", {}))])
    consumer = asyncio.create_task(_drive(migrated_db, tracer, adapter, reg))
    await asyncio.wait_for(started.wait(), timeout=2)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert cancelled.is_set()
    steps = await _rows(migrated_db, "agent:step")
    assert steps[0]["status"] == "error"


async def test_empty_turn_is_retried_once_and_traced(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A model turn with neither text nor tool calls (seen live on Gemini) is
    asked again once instead of ending the turn with no answer."""
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([ToolTurn(content="", usage={"finish_reason": "stop"}),
                           _answer("real answer")])
    events = await _drive(migrated_db, tracer, adapter, _read_registry())
    final = events[-1]
    assert isinstance(final, Final) and final.text == "real answer"
    steps = await _rows(migrated_db, "agent:step")
    assert steps[0]["result"]["empty_response"] is True
    assert steps[0]["result"]["finish_reason"] == "stop"
    assert steps[1]["result"]["answer"] == "real answer"
    retry = adapter.calls[1]["messages"]
    assert retry[-1]["role"] == "user" and "empty" in retry[-1]["content"]


async def test_repeated_empty_turn_falls_back_to_a_message(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([ToolTurn(content=""), ToolTurn(content="")])
    events = await _drive(migrated_db, tracer, adapter, _read_registry())
    final = events[-1]
    assert isinstance(final, Final) and final.text.startswith("I could not finish")
    assert len(adapter.calls) == 2


# ------------------------------------------------------------ fix round 1


async def test_tokens_stream_live_in_order_before_final(
    migrated_db: aiosqlite.Connection,
) -> None:
    """R7: answer tokens reach the consumer while the model turn is still
    running, in order, before Final."""
    _, _, tracer = await _setup(migrated_db)
    gate = asyncio.Event()
    seen: list[str] = []

    class Live:
        async def tool_turn(self, **kw: Any) -> ToolTurn:
            on_token = kw["on_token"]
            for piece in ("Hel", "lo ", "world"):
                await on_token(piece)
            # Only the consumer having received all three tokens opens this.
            await asyncio.wait_for(gate.wait(), timeout=2)
            return ToolTurn(content="Hello world")

    deps = AgentDeps(adapter=Live(), tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=_settings(), registry=_read_registry())
    events: list[AgentEvent] = []
    async for e in run_core_agent(
            {"run_id": 1, "session_id": 1, "user_message": "q", "history": []}, deps):
        events.append(e)
        if isinstance(e, Token):
            seen.append(e.text)
            if len(seen) == 3:
                gate.set()
    assert seen == ["Hel", "lo ", "world"]
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("Final") > max(i for i, k in enumerate(kinds) if k == "Token")
    final = events[-1]
    assert isinstance(final, Final) and final.text == "Hello world"


async def test_preamble_then_tool_call_yields_answer_reset(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def probe(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="ok", record={},
                           events=[("search_results", {"candidates": []})])

    reg = ToolRegistry()
    reg.register(ToolSpec("probe", "p", {"type": "object", "properties": {}},
                          Tier.FREE, probe))
    preamble = ToolTurn(content="Let me check.", tool_calls=[
        ToolCall(id="c0", name="probe", arguments={})])
    adapter = FakeAdapter([preamble, _answer("Done.")])
    events = await _drive(migrated_db, tracer, adapter, reg)
    names = [(type(e).__name__, getattr(e, "name", None)) for e in events]
    reset = names.index(("ToolEvent", "answer_reset"))
    assert reset < names.index(("ToolEvent", "search_results"))
    before = [e for e in events[:reset] if isinstance(e, Token)]
    assert "".join(t.text for t in before) == "Let me check."
    final = events[-1]
    assert isinstance(final, Final) and final.text == "Done."


async def test_no_answer_reset_without_preamble(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([_calls(("read_chunks", {"ids": [1]})), _answer("x")])
    events = await _drive(migrated_db, tracer, adapter, _read_registry())
    assert not any(isinstance(e, ToolEvent) and e.name == "answer_reset"
                   for e in events)


async def test_retried_pending_confirm_is_not_run_again(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def remove(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        raise AssertionError("must not run without confirmation")

    reg = ToolRegistry()
    reg.register(ToolSpec("drop_thing", "r",
                          {"type": "object", "properties": {"paper": {"type": "integer"}},
                           "required": ["paper"]}, Tier.CONFIRM, remove))
    adapter = FakeAdapter([
        _calls(("drop_thing", {"paper": 3})),
        _calls(("drop_thing", {"paper": 3})),  # the model retries
        _answer("Waiting for you to confirm."),
    ])
    events = await _drive(migrated_db, tracer, adapter, reg)
    confirms = [e for e in events if isinstance(e, ToolEvent)
                and e.name == "confirm_request"]
    assert len(confirms) == 1
    assert len(await _rows(migrated_db, "tool:drop_thing")) == 1
    steps = await _rows(migrated_db, "agent:step")
    assert steps[1]["result"]["skipped_pending_retry"] is True
    assert steps[1]["result"]["tool_results"][0]["status"] == "pending"
    tool_msg = [m for m in adapter.calls[2]["messages"] if m["role"] == "tool"][-1]
    assert "still waiting" in tool_msg["content"].lower()


async def test_empty_retry_does_not_consume_a_budget_step(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def noop(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="nothing", record={})

    reg = ToolRegistry()
    reg.register(ToolSpec("noop", "n", {"type": "object", "properties": {}},
                          Tier.FREE, noop))
    script: list[ToolTurn] = [ToolTurn(content="")] + [
        _calls(("noop", {})) for _ in range(AGENT_MAX_STEPS)] + [_answer("final")]
    adapter = FakeAdapter(script)
    events = await _drive(migrated_db, tracer, adapter, reg)
    # 1 empty retry + 12 tool steps + 1 forced answer
    assert len(adapter.calls) == AGENT_MAX_STEPS + 2
    assert adapter.calls[-1]["tool_choice"] == "none"
    assert all(c["tool_choice"] == "auto" for c in adapter.calls[:-1])
    final = events[-1]
    assert isinstance(final, Final) and final.record["empty_retries"] == 1


async def test_read_ids_deduped_within_one_outcome(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def dup(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="x", record={}, chunk_ids_read=[5, 5, 6])

    reg = ToolRegistry()
    reg.register(ToolSpec("dup", "d", {"type": "object", "properties": {}},
                          Tier.FREE, dup))
    adapter = FakeAdapter([_calls(("dup", {})), _answer("ok")])
    events = await _drive(migrated_db, tracer, adapter, reg)
    final = events[-1]
    assert isinstance(final, Final) and final.record["read"] == [5, 6]


async def test_adapter_error_writes_partial_record_and_reraises(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def noop(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="x", record={}, chunk_ids_read=[7])

    reg = ToolRegistry()
    reg.register(ToolSpec("noop", "n", {"type": "object", "properties": {}},
                          Tier.FREE, noop))

    def script(kw: dict[str, Any]) -> ToolTurn:
        if len(adapter.calls) == 1:
            return _calls(("noop", {}))
        raise RuntimeError("provider down")

    adapter = FakeAdapter(script)
    with pytest.raises(RuntimeError, match="provider down"):
        await _drive(migrated_db, tracer, adapter, reg)
    async with migrated_db.execute(
        "SELECT agent_record_json FROM runs WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] is not None
    record = json.loads(row[0])
    assert record["tools"] == ["noop"] and record["read"] == [7]
    assert record["error"] == "provider down"


async def test_idea_id_recorded_when_state_carries_idea_context(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.0001", {"A": ["a"]})
    await migrated_db.execute(
        "INSERT INTO ideas (source_doc, source_section, title) VALUES (?, 'A', 'T')",
        (pid,))
    await migrated_db.execute(
        "INSERT INTO idea_sessions (idea_id, session_id) VALUES (1, 1)")
    await migrated_db.commit()
    adapter = FakeAdapter([_answer("ok")])
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=_settings(), registry=_read_registry())
    _ = [e async for e in run_core_agent(
        {"run_id": 1, "session_id": 1, "user_message": "q", "history": [],
         "idea_context": "<idea_context>given</idea_context>"}, deps)]
    steps = await _rows(migrated_db, "agent:step")
    assert steps[0]["result"]["session_state"]["idea_id"] == 1


async def test_duplicate_confirm_calls_in_one_step_make_one_pending_action(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)

    async def remove(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        raise AssertionError("must not run without confirmation")

    reg = ToolRegistry()
    reg.register(ToolSpec("drop_thing", "r",
                          {"type": "object", "properties": {"paper": {"type": "integer"}},
                           "required": ["paper"]}, Tier.CONFIRM, remove))
    adapter = FakeAdapter([
        _calls(("drop_thing", {"paper": 3}), ("drop_thing", {"paper": 3})),
        _answer("Waiting for you to confirm."),
    ])
    events = await _drive(migrated_db, tracer, adapter, reg)
    confirms = [e for e in events if isinstance(e, ToolEvent)
                and e.name == "confirm_request"]
    assert len(confirms) == 1
    assert len(await _rows(migrated_db, "tool:drop_thing")) == 1
    async with migrated_db.execute("SELECT COUNT(*) FROM pending_actions") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1
    steps = await _rows(migrated_db, "agent:step")
    results = steps[0]["result"]["tool_results"]
    assert [r["status"] for r in results] == ["pending", "pending"]
    assert results[1]["skipped_pending_retry"] is True
    assert steps[0]["result"]["skipped_pending_retry"] is True
    tool_msgs = [m for m in adapter.calls[1]["messages"] if m["role"] == "tool"]
    assert "still waiting" in tool_msgs[1]["content"].lower()


def _final_registry(answer: str | None) -> ToolRegistry:
    async def lib(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content=f"lib: {answer}", record={"answer": answer},
                           final_answer=answer)

    async def other(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="other done", record={})

    reg = ToolRegistry()
    obj = {"type": "object", "properties": {}, "required": []}
    reg.register(ToolSpec("lib", "library", obj, Tier.FREE, lib))
    reg.register(ToolSpec("other", "other", obj, Tier.FREE, other))
    return reg


async def test_tool_final_answer_ends_the_turn_without_a_second_model_step(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A lone call whose outcome carries ``final_answer`` (query_library with
    answer_user) answers the user directly: no second model turn, the answer
    streams as a Token, and the step row records where it came from."""
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([_calls(("lib", {}))])
    events = await _drive(migrated_db, tracer, adapter, _final_registry("62 papers."))
    assert len(adapter.calls) == 1
    final = next(e for e in events if isinstance(e, Final))
    assert final.text == "62 papers."
    assert "".join(e.text for e in events if isinstance(e, Token)) == "62 papers."
    steps = await _rows(migrated_db, "agent:step")
    assert len(steps) == 1
    assert steps[0]["result"]["answer"] == "62 papers."
    assert steps[0]["result"]["answer_from_tool"] == "lib"
    assert [c["name"] for c in steps[0]["result"]["tool_calls"]] == ["lib"]
    async with migrated_db.execute(
            "SELECT agent_record_json FROM runs WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row is not None and json.loads(row[0])["tools"] == ["lib"]


async def test_tool_final_answer_ignored_beside_other_calls(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([_calls(("lib", {}), ("other", {})), _answer("combined")])
    events = await _drive(migrated_db, tracer, adapter, _final_registry("62 papers."))
    assert len(adapter.calls) == 2
    assert next(e for e in events if isinstance(e, Final)).text == "combined"
    steps = await _rows(migrated_db, "agent:step")
    assert "answer_from_tool" not in steps[0]["result"]


async def test_empty_tool_final_answer_falls_through_to_the_model(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([_calls(("lib", {})), _answer("model answer")])
    events = await _drive(migrated_db, tracer, adapter, _final_registry(""))
    assert len(adapter.calls) == 2
    assert next(e for e in events if isinstance(e, Final)).text == "model answer"


async def test_tool_final_answer_ignored_after_the_first_tool_step(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Step 2 has already read or acted: the library's answer would not
    include that, so the core model writes the answer."""
    _, _, tracer = await _setup(migrated_db)
    adapter = FakeAdapter([_calls(("other", {})), _calls(("lib", {})),
                           _answer("model answer")])
    events = await _drive(migrated_db, tracer, adapter, _final_registry("62 papers."))
    assert len(adapter.calls) == 3
    assert next(e for e in events if isinstance(e, Final)).text == "model answer"
    steps = await _rows(migrated_db, "agent:step")
    assert all("answer_from_tool" not in s["result"] for s in steps)


async def test_preamble_then_tool_final_answer_streams_in_order(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A streamed preamble before the lone call is cleared (answer_reset)
    before the tool's answer streams."""
    _, _, tracer = await _setup(migrated_db)
    turn = ToolTurn(content="Let me check.", tool_calls=[
        ToolCall(id="c0", name="lib", arguments={})])
    adapter = FakeAdapter([turn])
    events = await _drive(migrated_db, tracer, adapter, _final_registry("62 papers."))
    seq = [("token", e.text) if isinstance(e, Token)
           else ("event", e.name) if isinstance(e, ToolEvent)
           else ("final", e.text) if isinstance(e, Final) else ("step", "")
           for e in events]
    reset = seq.index(("event", "answer_reset"))
    pre = [i for i, x in enumerate(seq) if x[0] == "token" and x[1] != "62 papers."]
    ans = seq.index(("token", "62 papers."))
    assert pre and max(pre) < reset < ans < seq.index(("final", "62 papers."))
    assert len(adapter.calls) == 1


async def test_running_tools_and_later_steps_send_a_live_beat(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A tool that is still running has no tool_calls row yet (rows are
    written when they close), so the trace would show nothing during a long
    ``gather``. A live-only beat (negative step_index, never persisted) names
    the running tools while they run, and each later model step sends a
    'thinking' beat so the finished tool's beat does not linger."""
    _, _, tracer = await _setup(migrated_db)
    release = asyncio.Event()
    beats_before_release: list[str] = []

    async def slow(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        # Bounded: without a beat nothing releases the tool.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), timeout=2)
        return ToolOutcome(content="done", record={})

    reg = ToolRegistry()
    reg.register(ToolSpec("slow", "s", {"type": "object", "properties": {}},
                          Tier.FREE, slow))
    adapter = FakeAdapter([_calls(("slow", {}), ("slow", {"x": 1})),
                           _answer("ok")])
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=_settings(), registry=reg)
    state: dict[str, Any] = {"run_id": 1, "session_id": 1,
                             "user_message": "q", "history": []}
    events: list[AgentEvent] = []
    async for e in run_core_agent(state, deps):  # type: ignore[arg-type]
        events.append(e)
        if (isinstance(e, ToolEvent) and e.name == "tool_step"
                and not release.is_set()):
            beats_before_release.append(e.payload["record"]["tool"])
            release.set()
    beats = [e.payload["record"] for e in events
             if isinstance(e, ToolEvent) and e.name == "tool_step"]
    assert beats_before_release == ["agent:slow"]
    assert all(b["step_index"] < 0 for b in beats)
    assert [b["tool"] for b in beats] == ["agent:slow", "agent:thinking"]
    # live-only: nothing persisted with a negative index
    async with migrated_db.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE step_index < 0") as cur:
        assert (await cur.fetchone())[0] == 0  # type: ignore[index]


def _add_remove_registry(pid_to_add: int) -> ToolRegistry:
    """Read tools plus the real add_paper / remove_paper handlers' effect
    (add_paper's dispatcher is stubbed to link ``pid_to_add``)."""
    from paperhub.agents.core import action_tools

    r = _read_registry()

    async def add(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        await ctx.conn.execute(
            "INSERT INTO papers (session_id, paper_content_id) VALUES (?, ?)",
            (ctx.session_id, pid_to_add))
        await ctx.conn.commit()
        action_tools.mark_paper_enabled(ctx, pid_to_add, "New paper")
        return ToolOutcome(content="Added", record={"added": True})

    r.register(ToolSpec("add_paper", "add", {"type": "object", "properties": {}},
                        Tier.SHOW_UNDO, add))
    r.register(ToolSpec("remove_paper", "remove",
                        {"type": "object", "properties": {}},
                        Tier.SHOW_UNDO, action_tools._remove_paper))
    return r


async def test_paper_added_mid_turn_is_readable_in_the_next_step(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review #2: search -> add -> read in one turn (scenario 5), starting
    from a chat with no papers."""
    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.5", {"Intro": ["alpha text"]})
    adapter = FakeAdapter([
        _calls(("add_paper", {"paper_id": "arxiv:2401.5"})),
        _calls(("list_sections", {"paper": pid})),
        _answer("done"),
    ])
    await _drive(migrated_db, tracer, adapter, _add_remove_registry(pid))
    rows = await _rows(migrated_db, "tool:list_sections")
    assert rows and rows[0]["status"] == "ok", rows


async def test_paper_enabled_by_another_request_mid_turn_is_seen(
    migrated_db: aiosqlite.Connection,
) -> None:
    """The enabled set is re-read each step (e.g. a paper attached from the
    UI or a confirmed action while the turn runs)."""
    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.6", {"Intro": ["beta text"]})

    def script(kw: dict[str, Any]) -> ToolTurn:
        n = len(adapter.calls)
        if n == 1:
            return _calls(("list_sections", {"paper": 999}))
        if n == 2:
            return _calls(("list_sections", {"paper": pid}))
        return _answer("done")

    adapter = FakeAdapter(script)

    registry = _read_registry()
    orig = registry.run

    async def run(ctx: ToolContext, call: ToolCall) -> ToolOutcome:
        out = await orig(ctx, call)
        if call.arguments.get("paper") == 999:
            await _enable(migrated_db, pid)
        return out

    registry.run = run  # type: ignore[method-assign]
    await _drive(migrated_db, tracer, adapter, registry)
    rows = await _rows(migrated_db, "tool:list_sections")
    assert [r["status"] for r in rows] == ["error", "ok"], rows


async def test_paper_removed_mid_turn_is_no_longer_readable(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.7", {"Intro": ["gamma text"]})
    await _enable(migrated_db, pid)
    adapter = FakeAdapter([
        _calls(("remove_paper", {"paper": pid})),
        _calls(("list_sections", {"paper": pid})),
        _answer("done"),
    ])
    await _drive(migrated_db, tracer, adapter, _add_remove_registry(pid))
    rows = await _rows(migrated_db, "tool:list_sections")
    assert rows and rows[0]["status"] == "error", rows


async def test_deck_tools_in_one_step_run_in_call_order(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review #6: deck-writing tools share one deck, so a step's create_deck
    + write_notes (or two edit_slides) run one after another, in call order,
    while other tools still run beside them."""
    _, _, tracer = await _setup(migrated_db)
    log: list[str] = []

    def handler(name: str, delay: float) -> Any:
        async def run(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
            log.append(f"start {name}")
            await asyncio.sleep(delay)
            log.append(f"end {name}")
            return ToolOutcome(content=name, record={})
        return run

    r = ToolRegistry()
    empty = {"type": "object", "properties": {}}
    from paperhub.agents.core.action_tools import DECK_TOOLS

    assert {"create_deck", "edit_slides", "write_notes"} <= DECK_TOOLS
    r.register(ToolSpec("create_deck", "d", empty, Tier.IF_ASKED,
                        handler("create_deck", 0.05), serial=True))
    r.register(ToolSpec("write_notes", "n", empty, Tier.IF_ASKED,
                        handler("write_notes", 0.0), serial=True))
    r.register(ToolSpec("free", "f", empty, Tier.FREE, handler("free", 0.0)))
    adapter = FakeAdapter([
        _calls(("create_deck", {}), ("write_notes", {}), ("free", {})),
        _answer("done"),
    ])
    await _drive(migrated_db, tracer, adapter, r)
    assert log.index("end create_deck") < log.index("start write_notes")
    assert log.index("start free") < log.index("end create_deck")


async def test_stop_after_a_side_effect_saves_its_ui_event(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review #5: Stop during a slow call, after a call of the same step
    already attached a paper: the record keeps the chip's event (even though
    the step never finished), so the stopped turn can still show Undo."""
    _, _, tracer = await _setup(migrated_db)
    started = asyncio.Event()

    async def hang(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        started.set()
        await asyncio.sleep(30)
        return ToolOutcome(content="never", record={})

    async def add(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="Added", record={"added": True}, events=[
            ("paper_added", {"papers_id": 5, "paper_content_id": 9, "title": "T"})])

    reg = ToolRegistry()
    empty = {"type": "object", "properties": {}}
    reg.register(ToolSpec("hang", "h", empty, Tier.FREE, hang))
    reg.register(ToolSpec("add_paper", "a", empty, Tier.SHOW_UNDO, add))
    # gather-like call first: add_paper's event waits behind it in call order
    adapter = FakeAdapter([_calls(("hang", {}), ("add_paper", {}))])
    consumer = asyncio.create_task(_drive(migrated_db, tracer, adapter, reg))
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.05)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    async with migrated_db.execute(
        "SELECT agent_record_json FROM runs WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0]
    record = json.loads(row[0])
    assert record["cancelled"] is True
    assert record["ui_events"] == [{"event": "paper_added", "data": {
        "papers_id": 5, "paper_content_id": 9, "title": "T"}}]


async def test_papers_read_counts_only_papers_actually_read(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Carry-forward D: a remove-only turn read no paper; the turn summary
    (badge "read N papers") must not count the removed one."""
    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.8", {"Intro": ["delta text"]})
    other, _ = await _paper(migrated_db, "2401.9", {"Intro": ["eps text"]})
    await _enable(migrated_db, pid)
    await _enable(migrated_db, other)
    adapter = FakeAdapter([
        _calls(("remove_paper", {"paper": pid})),
        _answer("Removed."),
    ])
    events = await _drive(migrated_db, tracer, adapter, _add_remove_registry(pid))
    final = events[-1]
    assert isinstance(final, Final) and final.record["papers"] == []
    adapter = FakeAdapter([
        _calls(("list_sections", {"paper": other})),
        _answer("Sections."),
    ])
    events = await _drive(migrated_db, tracer, adapter, _add_remove_registry(pid))
    final = events[-1]
    assert isinstance(final, Final) and final.record["papers"] == [other]


async def test_stop_while_the_caller_handles_an_event_saves_the_record(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Live finding: Stop landed while the chat layer was forwarding the
    paper_added event (the loop suspended at its yield), so no CancelledError
    reached the loop and nothing was saved. Closing the loop then must save
    the record with the chip's event."""
    _, _, tracer = await _setup(migrated_db)

    async def add(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="Added", record={"added": True}, events=[
            ("paper_added", {"papers_id": 5, "paper_content_id": 9, "title": "T"})])

    reg = ToolRegistry()
    reg.register(ToolSpec("add_paper", "a", {"type": "object", "properties": {}},
                          Tier.SHOW_UNDO, add))
    adapter = FakeAdapter([_calls(("add_paper", {})), _answer("never")])
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=_settings(), registry=reg)
    agen = run_core_agent({"run_id": 1, "session_id": 1, "user_message": "q",  # type: ignore[arg-type]
                           "history": []}, deps)
    async for ev in agen:
        if isinstance(ev, ToolEvent) and ev.name == "paper_added":
            break
    deps.stop_requested = True  # the chat layer's Stop branch sets it
    await agen.aclose()
    async with migrated_db.execute(
        "SELECT agent_record_json FROM runs WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0]
    record = json.loads(row[0])
    assert record["cancelled"] is True
    assert [e["event"] for e in record["ui_events"]] == ["paper_added"]


async def test_closing_the_loop_without_a_stop_records_no_cancel(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Re-review N2: a caller-side error leaves the loop to be closed (or
    garbage-collected) at a yield; that is not a Stop, so it must not write a
    `cancelled` record over the errored run's."""
    _, _, tracer = await _setup(migrated_db)

    async def add(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="Added", record={"added": True}, events=[
            ("paper_added", {"papers_id": 5, "paper_content_id": 9, "title": "T"})])

    reg = ToolRegistry()
    reg.register(ToolSpec("add_paper", "a", {"type": "object", "properties": {}},
                          Tier.SHOW_UNDO, add))
    adapter = FakeAdapter([_calls(("add_paper", {})), _answer("never")])
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=_settings(), registry=reg)
    await migrated_db.execute(
        "UPDATE runs SET agent_record_json = '{\"error\": \"boom\"}' WHERE id = 1")
    await migrated_db.commit()
    agen = run_core_agent({"run_id": 1, "session_id": 1, "user_message": "q",  # type: ignore[arg-type]
                           "history": []}, deps)
    async for ev in agen:
        if isinstance(ev, ToolEvent) and ev.name == "paper_added":
            break
    await agen.aclose()
    async with migrated_db.execute(
        "SELECT agent_record_json FROM runs WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row is not None and json.loads(row[0]) == {"error": "boom"}


async def test_figure_images_follow_the_step_tool_messages(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A ``view_figure`` image reaches the next model call as one user
    message after the step's tool messages; the trace never holds pixels."""
    from paperhub.agents.core.figures import IMAGES_HEADER, FigureImage

    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.0009", {"Results": ["r"]})
    await _enable(migrated_db, pid)
    img = FigureImage(paper=pid, figure_id="fig-003", caption="Fig. 3", page=4,
                      mime="image/png", data_b64="SU1BR0VEQVRB", size=(8, 4),
                      sha256="a" * 64)

    async def view(_ctx: ToolContext, _args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content='<figure id="fig-003">', images=[img],
                           record={"figure": "fig-003", "sha256": img.sha256})

    async def sections(_ctx: ToolContext, _args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="Results", record={})

    reg = ToolRegistry()
    reg.register(ToolSpec("view_figure", "d", {"type": "object"}, Tier.FREE, view))
    reg.register(ToolSpec("list_sections", "d", {"type": "object"}, Tier.FREE, sections))
    adapter = FakeAdapter([
        _calls(("view_figure", {"paper": pid, "figure": "Figure 3"}),
               ("list_sections", {"paper": pid})),
        _answer("Four bar charts; the highest is about 77% AFNR."),
    ])
    await _drive(migrated_db, tracer, adapter, reg)

    sent = adapter.calls[1]["messages"]
    roles = [m["role"] for m in sent[-4:]]
    assert roles == ["assistant", "tool", "tool", "user"]
    parts = sent[-1]["content"]
    assert parts[0] == {"type": "text", "text": IMAGES_HEADER}
    assert {"type": "image_url",
            "image_url": {"url": "data:image/png;base64,SU1BR0VEQVRB"}} in parts
    async with migrated_db.execute(
        "SELECT group_concat(coalesce(result_summary_json, ''), ' ') "
        "FROM tool_calls WHERE run_id = 1",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and "SU1BR0VEQVRB" not in str(row[0])
    step = (await _rows(migrated_db, "agent:step"))[1]["result"]
    assert step["context_tokens"]["this_turn"] < 200  # base64 is not counted as text


async def test_figure_citations_need_a_viewed_figure_and_carry_to_the_next_turn(
    migrated_db: aiosqlite.Connection,
) -> None:
    """``[fig:P/ID]`` survives only for a figure viewed this turn (or kept
    from an earlier turn's work record); the work record lists it."""
    from paperhub.agents.core.figures import FigureImage

    _, _, tracer = await _setup(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.0010", {"Results": ["r"]})
    await _enable(migrated_db, pid)
    img = FigureImage(paper=pid, figure_id="fig-003", caption="Fig. 3", page=4,
                      mime="image/png", data_b64="QQ==", size=(2, 1), sha256="b" * 64)

    async def view(_ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="<figure>", images=[img],
                           record={"paper": args["paper"], "figure": "fig-003"})

    reg = ToolRegistry()
    reg.register(ToolSpec("view_figure", "d", {"type": "object"}, Tier.FREE, view))
    adapter = FakeAdapter([
        _calls(("view_figure", {"paper": pid, "figure": "Figure 3"})),
        _answer(f"About 77% [fig:{pid}/fig-003]; also [fig:{pid}/fig-009]."),
    ])
    events = await _drive(migrated_db, tracer, adapter, reg)
    final = events[-1]
    assert isinstance(final, Final)
    assert final.text == f"About 77% [fig:{pid}/fig-003]; also [unsourced:figure_not_viewed]."
    assert final.record["figures"] == [f"{pid}/fig-003"]
    assert final.record["cited_figures"] == [f"{pid}/fig-003"]
    step = (await _rows(migrated_db, "agent:step"))[-1]["result"]
    assert step["figure_citations_removed"] == [f"{pid}/fig-009"]

    # Next turn: the earlier figure is citable without viewing it again.
    await migrated_db.execute(
        "UPDATE runs SET agent_record_json = ? WHERE id = 1",
        (json.dumps(final.record),))
    await migrated_db.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (1, 'assistant', ?, 1)", (final.text,))
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer2 = Tracer(migrated_db, run_id=2, branch="")
    adapter2 = FakeAdapter([_answer(f"As shown before [fig:{pid}/fig-003].")])
    deps = AgentDeps(adapter=adapter2, tracer=tracer2, conn=migrated_db,  # type: ignore[arg-type]
                     settings=_settings(), registry=reg)
    state: dict[str, Any] = {
        "run_id": 2, "session_id": 1, "user_message": "and again?",
        "history": [{"role": "user", "content": "question"},
                    {"role": "assistant", "content": final.text, "run_id": 1}]}
    events2 = [e async for e in run_core_agent(state, deps)]  # type: ignore[arg-type]
    final2 = events2[-1]
    assert isinstance(final2, Final)
    assert final2.text == f"As shown before [fig:{pid}/fig-003]."
    sent = adapter2.calls[0]["messages"]
    assert any(f"figures: {pid}/fig-003" in str(m.get("content")) for m in sent)
