"""Tests for the gather tool (H4 Task 5): a small-model subagent that returns
verbatim chunks it actually read."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.gather import GATHER_MAX_READS, register_gather_tool
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall, ToolTurn
from paperhub.tracing.tracer import Tracer

from .test_read_tools import _paper

pytestmark = pytest.mark.asyncio


class FakeAdapter:
    def __init__(self, turns: list[ToolTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def tool_turn(self, **kw: Any) -> ToolTurn:
        self.calls.append(kw)
        return self.turns.pop(0)


def _turn(*calls: tuple[str, dict[str, Any]]) -> ToolTurn:
    return ToolTurn(
        content="",
        tool_calls=[
            ToolCall(id=f"call_{i}__thought__abc==", name=n, arguments=a)
            for i, (n, a) in enumerate(calls)
        ],
    )


def _ctx(
    conn: aiosqlite.Connection, tracer: Tracer, adapter: FakeAdapter,
    enabled: list[tuple[int, str]],
) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1,
        settings=SimpleNamespace(agent_gather_model="gather-model"),  # type: ignore[arg-type]
        adapter=adapter,  # type: ignore[arg-type]
        emit=emit, enabled_papers=enabled, history=[], state={},
    )


async def _gather(ctx: ToolContext, **args: Any) -> ToolOutcome:
    reg = ToolRegistry()
    register_gather_tool(reg)
    return await reg.run(ctx, ToolCall(id="g1", name="gather", arguments=args))


def _select(*items: tuple[int, str]) -> tuple[str, dict[str, Any]]:
    return ("select", {"chunks": [{"id": i, "reason": r} for i, r in items]})


async def test_returns_only_chunks_it_read_verbatim(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    pid, ids = await _paper(
        migrated_db, "2401.0001",
        {"Loss": ["The loss is L = a + b.", "We add a sparsity term."],
         "Intro": ["Unrelated intro text."]},
    )
    a = ids["The loss is L = a + b."]
    adapter = FakeAdapter([
        _turn(("read_section", {"paper": pid, "section": "Loss"})),
        _turn(_select((a, "states the loss"))),
    ])
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(pid, "Title 2401.0001")]),
        papers=[pid], brief="the loss function",
    )
    assert f'<chunk id="{a}" paper="Title 2401.0001" section="Loss">' in out.content
    assert "The loss is L = a + b.\n</chunk>\nreason: states the loss" in out.content
    assert "Unrelated" not in out.content
    assert out.record["chunk_ids_selected"] == [a]
    assert out.record["reasons"] == {str(a): "states the loss"}
    assert out.record["sections_read"] == [{"paper": pid, "section": "Loss"}]
    assert out.chunk_ids_read == [a]
    # tool-call ids are echoed verbatim to the model
    second = adapter.calls[1]["messages"]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "call_0__thought__abc=="
    assert adapter.calls[0]["model"] == "gather-model"


async def test_unread_selected_id_is_dropped_and_recorded(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    pid, ids = await _paper(
        migrated_db, "2401.0001", {"A": ["read me"], "B": ["never read"]}
    )
    read, unread = ids["read me"], ids["never read"]
    adapter = FakeAdapter([
        _turn(("read_section", {"paper": pid, "section": "A"})),
        _turn(_select((read, "ok"), (unread, "guessed"), (987654, "bogus"))),
    ])
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(pid, "T")]),
        papers=[pid], brief="b",
    )
    assert out.record["chunk_ids_selected"] == [read]
    assert out.record["dropped_ids"] == [unread, 987654]
    assert "never read" not in out.content


async def test_budget_exhaustion_still_returns_selection(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    secs = {f"S{i}": [f"text of section {i}"] for i in range(GATHER_MAX_READS + 2)}
    pid, ids = await _paper(migrated_db, "2401.0001", secs)
    turns = [
        _turn(("read_section", {"paper": pid, "section": f"S{i}"}))
        for i in range(GATHER_MAX_READS + 1)
    ]
    keep = ids["text of section 0"]
    turns.append(_turn(_select((keep, "relevant"))))
    adapter = FakeAdapter(turns)
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(pid, "T")]),
        papers=[pid], brief="b",
    )
    assert out.record["chunk_ids_selected"] == [keep]
    assert len(out.record["sections_read"]) == GATHER_MAX_READS
    # the over-budget read was refused, not executed
    last_tool_msg = [m for m in adapter.calls[-1]["messages"] if m["role"] == "tool"][-1]
    assert "budget" in last_tool_msg["content"].lower()


async def test_no_select_call_forces_a_final_select(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    pid, ids = await _paper(migrated_db, "2401.0001", {"A": ["only chunk"]})
    cid = ids["only chunk"]
    adapter = FakeAdapter([
        _turn(("read_section", {"paper": pid, "section": "A"})),
        ToolTurn(content="I am done reading."),
        _turn(_select((cid, "it"))),
    ])
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(pid, "T")]),
        papers=[pid], brief="b",
    )
    assert out.record["chunk_ids_selected"] == [cid]
    assert adapter.calls[-1]["tool_choice"] == {
        "type": "function", "function": {"name": "select"}
    }


async def test_papers_must_be_enabled(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    a, _ = await _paper(migrated_db, "2401.0001", {"S": ["x"]})
    b, _ = await _paper(migrated_db, "2401.0002", {"S": ["y"]})
    adapter = FakeAdapter([])
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(a, "A")]), papers=[b], brief="b"
    )
    assert out.record.get("error") and "not enabled" in out.content
    assert adapter.calls == []


async def test_traced_with_nested_agent_gather_step(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    pid, ids = await _paper(migrated_db, "2401.0001", {"A": ["hello world"]})
    cid = ids["hello world"]
    adapter = FakeAdapter([
        _turn(("read_section", {"paper": pid, "section": "A"})),
        _turn(_select((cid, "greeting"))),
    ])
    await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(pid, "T")]),
        papers=[pid], brief="say hi",
    )
    async with migrated_db.execute(
        "SELECT tool, status, result_summary_json, args_redacted_json "
        "FROM tool_calls ORDER BY step_index"
    ) as cur:
        rows = await cur.fetchall()
    tools = [r[0] for r in rows]
    assert "tool:gather" in tools and "agent:gather" in tools
    inner = json.loads(next(r[2] for r in rows if r[0] == "agent:gather"))
    assert inner["selection"] == [{"id": cid, "reason": "greeting"}]
    assert inner["tool_calls"][0]["name"] == "read_section"
    assert inner["tool_calls"][0]["chunk_ids"] == [cid]
    inner_args = json.loads(next(r[3] for r in rows if r[0] == "agent:gather"))
    assert inner_args["brief"] == "say hi" and inner_args["papers"] == [pid]


async def test_out_of_scope_chunk_cannot_be_read_or_selected(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    a, ia = await _paper(migrated_db, "2401.0001", {"S": ["in scope text"]})
    b, ib = await _paper(migrated_db, "2401.0002", {"S": ["secret other paper"]})
    inside, outside = ia["in scope text"], ib["secret other paper"]
    adapter = FakeAdapter([
        _turn(("read_chunks", {"ids": [outside]}),
              ("search_chunks", {"query": "secret", "papers": [b]}),
              ("read_chunks", {"ids": [inside]})),
        _turn(_select((inside, "ok"), (outside, "sneaky"))),
    ])
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(a, "A"), (b, "B")]),
        papers=[a], brief="b",
    )
    msgs = [m["content"] for m in adapter.calls[1]["messages"] if m["role"] == "tool"]
    assert "outside this gather's papers" in msgs[0]
    assert "outside this gather's papers" in msgs[1]
    assert "secret other paper" not in out.content
    assert out.record["chunk_ids_selected"] == [inside]
    assert out.record["dropped_ids"] == [outside]
    assert out.record["dropped_reasons"] == {str(outside): "not_read"}


async def test_out_of_scope_selected_id_dropped_with_reason(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    a, ia = await _paper(migrated_db, "2401.0001", {"S": ["mine"]})
    b, ib = await _paper(migrated_db, "2401.0002", {"S": ["theirs"]})
    outside = ib["theirs"]
    adapter = FakeAdapter([_turn(_select((outside, "x")))])
    ctx = _ctx(migrated_db, fake_tracer, adapter, [(a, "A"), (b, "B")])
    # Simulate a slipped-through read: the id is marked read by a tool result.
    from paperhub.agents.core import gather as G

    orig = G._scope_error

    async def _allow(*_a: Any, **_k: Any) -> None:
        return None

    G._scope_error = _allow  # type: ignore[assignment]
    adapter.turns.insert(0, _turn(("read_chunks", {"ids": [outside]})))
    try:
        out = await _gather(ctx, papers=[a], brief="b")
    finally:
        G._scope_error = orig  # type: ignore[assignment]
    assert out.record["chunk_ids_selected"] == []
    assert out.record["dropped_reasons"] == {str(outside): "out_of_scope"}
    assert "theirs" not in out.content


@pytest.mark.parametrize("papers", [None, [], "7", [1, True], [1.5]])
async def test_invalid_papers_is_a_readable_error(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, papers: Any
) -> None:
    adapter = FakeAdapter([])
    ctx = _ctx(migrated_db, fake_tracer, adapter, [(1, "A")])
    args: dict[str, Any] = {"brief": "b"}
    if papers is not None:
        args["papers"] = papers
    out = await _gather(ctx, **args)
    assert out.record.get("error") and adapter.calls == []


async def test_empty_brief_is_a_readable_error(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    adapter = FakeAdapter([])
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(1, "A")]), papers=[1], brief="  "
    )
    assert "brief" in out.content and adapter.calls == []


async def test_unknown_tool_is_readable_and_loop_continues(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    pid, ids = await _paper(migrated_db, "2401.0001", {"A": ["text"]})
    cid = ids["text"]
    adapter = FakeAdapter([
        _turn(("search_section", {"q": "x"})),
        _turn(("read_section", {"paper": pid, "section": "A"})),
        _turn(_select((cid, "r"))),
    ])
    out = await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(pid, "T")]), papers=[pid], brief="b"
    )
    tool_msg = [m for m in adapter.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "Unknown tool" in tool_msg["content"] and "list_sections" in tool_msg["content"]
    assert out.record["chunk_ids_selected"] == [cid]


async def test_trace_records_search_hits_and_match_mode(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer
) -> None:
    """A search's hits must reach the agent:gather trace — otherwise every
    search looks empty and the trace cannot tell a miss from a hit."""
    pid, ids = await _paper(
        migrated_db, "2401.0002", {"A": ["expert load balance"]})
    cid = ids["expert load balance"]
    adapter = FakeAdapter([
        _turn(("search_chunks", {"query": "expert balance", "papers": [pid]})),
        _turn(("search_chunks", {"query": "expert zebra", "papers": [pid]})),
        _turn(_select()),
    ])
    await _gather(
        _ctx(migrated_db, fake_tracer, adapter, [(pid, "T")]),
        papers=[pid], brief="find balance",
    )
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'agent:gather'"
    ) as cur:
        (raw,) = await cur.fetchone()  # type: ignore[misc]
    searches = [c for c in json.loads(raw)["tool_calls"]
                if c["name"] == "search_chunks"]
    assert searches[0]["hit_ids"] == [cid]
    assert searches[0]["match"] == "all_terms"
    assert searches[1]["hit_ids"] == [cid]
    assert searches[1]["match"] == "any_term"
    assert searches[1]["query_as_sent"]
    assert searches[0]["chunk_ids"] == []  # a hit is a snippet, not a read
