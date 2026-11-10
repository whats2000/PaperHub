"""POST /chat drives the core agent (H4 Task 10, SRS §III-10.4).

Every turn runs the tool-calling core agent instead of ``router_node`` +
intent dispatch. The SSE stream keeps today's channels (``session``,
``tool_step``, ``token``, ``search_results``, ``deck``, ``final``,
``error``) and adds ``agent_summary``, ``answer_reset``, ``paper_added``,
``memory_saved`` and ``confirm_request``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paperhub.agents.core.tools import Tier, ToolOutcome, ToolRegistry, ToolSpec
from paperhub.agents.research import (
    FinalOnlyMessage,
    SearchCandidate,
    SearchResultsYield,
    ToolStepYield,
)
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema
from paperhub.llm.adapter import ToolCall, ToolTurn
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from tests.agent_script import calls as _calls
from tests.agent_script import script_agent as _script

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------- helpers


class _FakeMcpRegistry:
    async def aggregate_tool_schemas(self) -> list[Any]:
        return []

    async def has_tool(self, name: str) -> bool:
        return False

    async def call(self, name: str, args: dict[str, Any]) -> Any:  # pragma: no cover
        raise RuntimeError(f"unexpected MCP call {name!r}")


def _app() -> FastAPI:
    app = create_app()
    app.state.mcp_registry = _FakeMcpRegistry()
    return app


async def _bootstrap(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await apply_schema(conn)


@pytest.fixture(autouse=True)
def _clear_broker() -> Iterator[None]:
    import paperhub.api.chat as chat_module

    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()
    yield
    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()


def _candidate(pid: str, title: str) -> SearchCandidate:
    return SearchCandidate(
        paper_id=pid, title=title, authors=["A. Author"], year=2024,
        abstract=None, arxiv_id=None, has_open_pdf=False, reason="relevant",
        finalize=False,
    )


def _patch_paper_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """``search_papers`` calls ``chat.paper_search``; answer per query."""
    import paperhub.api.chat as chat_module

    async def fake(state: Any, **_kw: Any) -> AsyncIterator[Any]:
        q = str(state["effective_query"])
        yield SearchResultsYield(candidates=[_candidate(f"arxiv:{q}", f"Paper {q}")])
        yield FinalOnlyMessage("")

    monkeypatch.setattr(chat_module, "paper_search", fake)


async def _consume(stream: AsyncIterator[bytes]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    buf = ""
    async for chunk in stream:
        buf += chunk.decode("utf-8").replace("\r\n", "\n")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            name, data = "", ""
            for line in block.splitlines():
                if line.startswith("event: "):
                    name = line[len("event: "):]
                elif line.startswith("data: "):
                    data = line[len("data: "):]
            if name:
                events.append((name, json.loads(data) if data else {}))
    return events


async def _chat(
    app: FastAPI, message: str, session_id: int | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    async with AsyncClient(  # noqa: SIM117
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        async with client.stream(
            "POST", "/chat",
            json={"session_id": session_id, "user_message": message},
        ) as resp:
            assert resp.status_code == 200
            return await _consume(resp.aiter_bytes())


async def _row(sql: str, *args: Any) -> Any:
    async with (
        aiosqlite.connect(load_settings().db_path) as conn,
        conn.execute(sql, args) as cur,
    ):
        return await cur.fetchone()


# ----------------------------------------------------------------- tests


async def test_turn_streams_agent_events_in_order(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap(tmp_path, monkeypatch)
    _patch_paper_search(monkeypatch)
    _script(monkeypatch, [
        _calls(("search_papers", {"query": "q1"})),
        ToolTurn(content="Here is what I found."),
    ])

    events = await _chat(_app(), "find papers on q1")
    types = [t for t, _ in events]

    assert types[0] == "session"
    assert "routing_decision" not in types
    assert types[-2:] == ["agent_summary", "final"]
    first_tool_step = types.index("tool_step")
    first_token = types.index("token")
    assert first_tool_step < first_token
    steps = [d["record"] for t, d in events
             if t == "tool_step" and d["record"]["step_index"] >= 0]
    indexes = [r["step_index"] for r in steps]
    assert len(indexes) == len(set(indexes)), "a trace row was emitted twice"
    tools = {r["tool"] for r in steps}
    assert {"agent:step", "tool:search_papers"} <= tools
    assert "router" not in " ".join(tools)

    summary = dict(events)["agent_summary"]
    run_id = dict(events)["session"]["run_id"]
    assert summary == {"run_id": run_id, "papers_read": 0, "steps": 2,
                       "tools": ["search_papers"]}
    final = dict(events)["final"]
    assert final["content"] == "Here is what I found."

    mode, record_json, status = await _row(
        "SELECT mode, agent_record_json, status FROM runs WHERE id = ?", run_id)
    assert (mode, status) == ("agent", "ok")
    assert json.loads(record_json)["tools"] == ["search_papers"]
    (content,) = await _row(
        "SELECT content FROM messages WHERE run_id = ? AND role = 'assistant'",
        run_id)
    assert content == "Here is what I found."
    # Every trace row reached the stream.
    (n_rows,) = await _row("SELECT COUNT(*) FROM tool_calls WHERE run_id = ?", run_id)
    assert n_rows == len(indexes)


async def test_final_replaces_streamed_text_and_answer_reset_passes(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A streamed preamble before a tool call is cleared with answer_reset;
    the final event carries the citation-validated answer."""
    await _bootstrap(tmp_path, monkeypatch)
    _patch_paper_search(monkeypatch)
    preamble = ToolTurn(content="Let me look.", tool_calls=[
        ToolCall(id="c0", name="search_papers", arguments={"query": "q"})])
    # [chunk:99] was never read -> stripped from the final answer.
    _script(monkeypatch, [preamble, ToolTurn(content="Answer [chunk:99].")])

    events = await _chat(_app(), "hi")
    types = [t for t, _ in events]
    assert "answer_reset" in types
    assert types.index("answer_reset") < types.index("search_results")
    final = dict(events)["final"]
    assert "[chunk:99]" not in final["content"]
    assert final["content"].startswith("Answer")


async def test_two_searches_persist_two_batches_and_replay(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap(tmp_path, monkeypatch)
    _patch_paper_search(monkeypatch)
    _script(monkeypatch, [
        _calls(("search_papers", {"query": "q1"}),
               ("search_papers", {"query": "q2"})),
        ToolTurn(content="Two lists."),
    ])
    app = _app()
    events = await _chat(app, "search twice")

    batches = [d for t, d in events if t == "search_results"]
    run_id = dict(events)["session"]["run_id"]
    session_id = dict(events)["session"]["session_id"]
    assert [b["candidates"][0]["paper_id"] for b in batches] == ["arxiv:q1", "arxiv:q2"]
    assert all(b["run_id"] == run_id for b in batches)

    (raw,) = await _row("SELECT search_results_json FROM runs WHERE id = ?", run_id)
    stored = json.loads(raw)
    assert [[c["paper_id"] for c in b] for b in stored["batches"]] == [
        ["arxiv:q1"], ["arxiv:q2"]]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(f"/sessions/{session_id}/messages")
    assert resp.status_code == 200
    assistant = [m for m in resp.json() if m["role"] == "assistant"][0]
    assert assistant["routing_decision"] is None
    assert [[c["paper_id"] for c in b] for b in assistant["search_result_batches"]] == [
        ["arxiv:q1"], ["arxiv:q2"]]
    assert [c["paper_id"] for c in assistant["search_results"]] == [
        "arxiv:q1", "arxiv:q2"]
    assert assistant["agent_summary"] == {
        "papers_read": 0, "steps": 2, "tools": ["search_papers"]}


async def test_pre_h4_pipeline_run_replays_routing_decision(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap(tmp_path, monkeypatch)
    decision = {"intent": "paper_qa", "confidence": 0.9, "reasoning": "asks"}
    record = {"tools": ["read_section", "read_section", "gather"],
              "read": [1, 2], "cited": [1], "papers": [3, 4], "steps": 3}
    async with aiosqlite.connect(load_settings().db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status, routing_decision_json) "
            "VALUES (1, 1, 'ok', ?)", (json.dumps(decision),))
        await conn.execute(
            "INSERT INTO runs (id, session_id, status, mode, agent_record_json) "
            "VALUES (2, 1, 'ok', 'agent', ?)", (json.dumps(record),))
        await conn.executemany(
            "INSERT INTO messages (session_id, role, content, run_id) VALUES (1, ?, ?, ?)",
            [("user", "old q", 1), ("assistant", "old a", 1),
             ("user", "new q", 2), ("assistant", "new a", 2)])
        await conn.commit()

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test",
    ) as client:
        resp = await client.get("/sessions/1/messages")
    old, new = [m for m in resp.json() if m["role"] == "assistant"]
    assert old["routing_decision"]["intent"] == "paper_qa"
    assert old["agent_summary"] is None
    assert new["routing_decision"] is None
    assert new["agent_summary"] == {
        "papers_read": 2, "steps": 3, "tools": ["read_section", "gather"]}


async def test_loop_error_emits_error_and_finalises(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap(tmp_path, monkeypatch)
    _script(monkeypatch, [RuntimeError("provider exploded")])

    events = await _chat(_app(), "hi")
    types = [t for t, _ in events]
    assert "error" in types and "final" not in types
    run_id = dict(events)["session"]["run_id"]
    status, record_json = await _row(
        "SELECT status, agent_record_json FROM runs WHERE id = ?", run_id)
    assert status == "error"
    assert "provider exploded" in json.loads(record_json)["error"]


async def test_cancel_during_slow_tool_cancels_the_tool_task(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review Focus 4: Stop mid-tool cancels the loop AND the tool task; the
    run ends cancelled with its messages retracted, nothing keeps writing."""
    await _bootstrap(tmp_path, monkeypatch)
    import paperhub.api.chat as chat_module

    started = asyncio.Event()
    cancelled = asyncio.Event()
    finished = asyncio.Event()

    async def slow(_ctx: Any, _args: dict[str, Any]) -> ToolOutcome:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finished.set()
        return ToolOutcome(content="done", record={})

    def registry(_settings: Any) -> ToolRegistry:
        r = ToolRegistry()
        r.register(ToolSpec("slow_tool", "slow", {"type": "object", "properties": {}},
                            Tier.FREE, slow))
        return r

    monkeypatch.setattr(chat_module, "build_default_registry", registry)
    _script(monkeypatch, [_calls(("slow_tool", {})), ToolTurn(content="never")])

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        session_id = await chat_module._ensure_session(conn, None)
        run_id = await chat_module._new_run(conn, session_id)
    handle = chat_module.broker.register(run_id)
    req = chat_module.ChatRequest(user_message="run the slow tool")
    handle.task = asyncio.create_task(chat_module.run_agent(
        handle, session_id, run_id, req, settings, LiteLlmAdapter(),
        None, None, None, _FakeMcpRegistry(),
    ))
    await asyncio.wait_for(started.wait(), timeout=10)

    await chat_module.cancel_run(chat_module.CancelRequest(run_id=run_id))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(handle.task, timeout=10)

    assert cancelled.is_set() and not finished.is_set()
    assert handle.status == "cancelled"
    (status,) = await _row("SELECT status FROM runs WHERE id = ?", run_id)
    assert status == "cancelled"
    (n_msgs,) = await _row("SELECT COUNT(*) FROM messages WHERE run_id = ?", run_id)
    assert n_msgs == 0
    # No orphan keeps writing after the cancel settles.
    (rows_then,) = await _row("SELECT COUNT(*) FROM tool_calls WHERE run_id = ?", run_id)
    await asyncio.sleep(0.3)
    (rows_now,) = await _row("SELECT COUNT(*) FROM tool_calls WHERE run_id = ?", run_id)
    assert rows_now == rows_then
    (n_msgs,) = await _row("SELECT COUNT(*) FROM messages WHERE run_id = ?", run_id)
    assert n_msgs == 0


async def test_style_command_intercepted_only_when_a_deck_exists(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deterministic "reset slide style" path stays (no LLM call) but
    only fires in a chat that has a deck; elsewhere the agent answers."""
    await _bootstrap(tmp_path, monkeypatch)
    seen = _script(monkeypatch, [ToolTurn(content="No deck here.")])
    app = _app()

    events = await _chat(app, "reset slide style")
    assert dict(events)["final"]["content"] == "No deck here."
    assert len(seen) == 1
    session_id = dict(events)["session"]["session_id"]

    async with aiosqlite.connect(load_settings().db_path) as conn:
        await conn.execute(
            "INSERT INTO decks (session_id, tex_path) VALUES (?, 'x.tex')",
            (session_id,))
        await conn.commit()
    events = await _chat(app, "reset slide style", session_id=session_id)
    assert len(seen) == 1, "the intercept must not call the model"
    final = dict(events)["final"]["content"]
    assert "slide style" in final.lower()
    run_id = dict(events)["session"]["run_id"]
    (tool,) = await _row(
        "SELECT tool FROM tool_calls WHERE run_id = ? ORDER BY step_index", run_id)
    assert tool == "agent:style_command"
    assert [t for t, _ in events][-2:] == ["agent_summary", "final"]


async def test_evicted_agent_run_replays_summary_before_final(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /chat/runs/{id}/events after the handle is gone (DB fallback):
    an agent run yields agent_summary then final, like the live stream."""
    await _bootstrap(tmp_path, monkeypatch)
    _script(monkeypatch, [ToolTurn(content="Done.")])
    app = _app()
    events = await _chat(app, "hi")
    run_id = dict(events)["session"]["run_id"]
    import paperhub.api.chat as chat_module

    chat_module.broker._handles.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(f"/chat/runs/{run_id}/events")
    body = resp.json()
    assert [e["event"] for e in body["events"]] == ["agent_summary", "final"]
    assert json.loads(body["events"][0]["data"])["steps"] == 1


async def test_stop_during_poller_teardown_keeps_the_run_cancelled(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Stop that lands while the turn awaits its trace poller's teardown
    must not be swallowed: the run stays 'cancelled' with no assistant
    message (no _finalise(status='ok') over the cancel)."""
    await _bootstrap(tmp_path, monkeypatch)
    import paperhub.api.chat as chat_module

    tearing_down = asyncio.Event()

    async def slow_teardown_poll(self: Any) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            tearing_down.set()
            await asyncio.sleep(0.5)
            raise

    monkeypatch.setattr(chat_module._TraceStream, "poll", slow_teardown_poll)
    _script(monkeypatch, [ToolTurn(content="Done.")])

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        session_id = await chat_module._ensure_session(conn, None)
        run_id = await chat_module._new_run(conn, session_id)
    handle = chat_module.broker.register(run_id)
    handle.task = asyncio.create_task(chat_module.run_agent(
        handle, session_id, run_id, chat_module.ChatRequest(user_message="hi"),
        settings, LiteLlmAdapter(), None, None, None, _FakeMcpRegistry(),
    ))
    await asyncio.wait_for(tearing_down.wait(), timeout=10)
    await chat_module.cancel_run(chat_module.CancelRequest(run_id=run_id))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(handle.task, timeout=10)

    (status,) = await _row("SELECT status FROM runs WHERE id = ?", run_id)
    assert status == "cancelled"
    (n_msgs,) = await _row(
        "SELECT COUNT(*) FROM messages WHERE run_id = ? AND role = 'assistant'",
        run_id)
    assert n_msgs == 0
    assert "final" not in [e["event"] for e in handle.events]


async def test_poller_failure_does_not_fail_the_turn(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB error inside a background trace drain is logged, not raised:
    the turn still ends ok."""
    await _bootstrap(tmp_path, monkeypatch)
    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "TRACE_POLL_SECONDS", 0.01)
    real_drain = chat_module.drain_tool_calls_since
    calls_seen = {"n": 0}

    async def flaky_drain(*args: Any, **kwargs: Any) -> Any:
        calls_seen["n"] += 1
        if calls_seen["n"] == 1:
            raise RuntimeError("database is locked")
        return await real_drain(*args, **kwargs)

    monkeypatch.setattr(chat_module, "drain_tool_calls_since", flaky_drain)

    async def slow_answer(_kw: dict[str, Any]) -> ToolTurn:
        await asyncio.sleep(0.1)  # let the poller tick (and fail) first
        return ToolTurn(content="Fine.")

    _script(monkeypatch, [slow_answer])
    events = await _chat(_app(), "hi")
    assert dict(events)["final"]["content"] == "Fine."
    assert "error" not in [t for t, _ in events]


async def test_deck_heartbeats_reach_the_stream(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report stage heartbeats (negative step_index, live-only) from a deck
    tool are forwarded — each of them, even when two stages share -1."""
    await _bootstrap(tmp_path, monkeypatch)
    import paperhub.api.chat as chat_module

    def beat(tool: str, idx: int) -> dict[str, Any]:
        return {"run_id": 1, "branch": "", "step_index": idx, "parent_step": None,
                "agent": "report", "tool": tool, "model": "",
                "args_redacted_json": None,
                "result_summary_json": {"stage": True, "elapsed_s": 0.0},
                "latency_ms": 0, "token_in": 0, "token_out": 0,
                "status": "ok", "error": None}

    async def fake_report_stream(state: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield ToolStepYield(record=beat("report:planning", -1))
        yield ToolStepYield(record=beat("report:drafting", -1))
        yield ToolStepYield(record=beat("report:drafting", -2))
        yield FinalOnlyMessage("deck done")

    monkeypatch.setattr(chat_module, "report_stream", fake_report_stream)
    _script(monkeypatch, [
        _calls(("create_deck", {"brief": "the papers"})),
        ToolTurn(content="Built."),
    ])
    events = await _chat(_app(), "make slides")
    beats = [(d["record"]["tool"], d["record"]["step_index"])
             for t, d in events
             if t == "tool_step" and d["record"]["step_index"] < 0]
    # the agent's own live beats frame the Report stage beats
    assert beats == [("agent:create_deck", -1), ("report:planning", -1),
                     ("report:drafting", -1), ("report:drafting", -2),
                     ("agent:thinking", -1)]


async def test_deck_heartbeat_streams_while_the_tool_runs(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling R10: a deck build takes minutes, so its stage heartbeats reach
    the SSE stream live, BEFORE the tool returns its deck card."""
    await _bootstrap(tmp_path, monkeypatch)
    import paperhub.api.chat as chat_module

    seen_live: dict[str, bool] = {}

    def on_stream(run_id: int) -> bool:
        handle = chat_module.broker.get(run_id)
        assert handle is not None
        return any(
            e["event"] == "tool_step"
            and json.loads(e["data"])["record"]["step_index"] == -1
            for e in handle.events)

    async def fake_report_stream(state: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield ToolStepYield(record={
            "run_id": state["run_id"], "branch": "", "step_index": -1,
            "parent_step": None, "agent": "report", "tool": "report:planning",
            "model": "", "args_redacted_json": None,
            "result_summary_json": {"stage": True, "elapsed_s": 0.0},
            "latency_ms": 0, "token_in": 0, "token_out": 0, "status": "ok",
            "error": None})
        for _ in range(100):  # wait (bounded) for the beat to be streamed
            if on_stream(int(state["run_id"])):
                break
            await asyncio.sleep(0.02)
        seen_live["beat"] = on_stream(int(state["run_id"]))
        yield chat_module.DeckYield(deck={
            "deck_id": 1, "session_id": 1, "page_count": 2, "title": "T",
            "status": "ok", "contributing_papers": [], "has_notes": False})
        yield FinalOnlyMessage("deck done")

    monkeypatch.setattr(chat_module, "report_stream", fake_report_stream)
    _script(monkeypatch, [
        _calls(("create_deck", {"brief": "the papers"})),
        ToolTurn(content="Built."),
    ])
    events = await _chat(_app(), "make slides")
    assert seen_live == {"beat": True}, "heartbeat was buffered until the tool returned"
    types = [t for t, _ in events]
    beat_at = next(i for i, (t, d) in enumerate(events)
                   if t == "tool_step" and d["record"]["step_index"] == -1)
    assert beat_at < types.index("deck")


def _use_ui_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """``add_and_save`` emits a paper chip and a memory chip; ``danger`` is
    a confirm-tier tool (auto-approve is off, so it asks)."""
    import paperhub.api.chat as chat_module

    async def add_and_save(_ctx: Any, _args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="ok", record={}, events=[
            ("paper_added", {"papers_id": 7, "paper_content_id": 3, "title": "T"}),
            ("memory_saved", {"memory_id": 5, "content": "likes X",
                              "scope": "global"}),
        ])

    async def danger(_ctx: Any, _args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(content="ran", record={})  # pragma: no cover

    def registry(_settings: Any) -> ToolRegistry:
        r = ToolRegistry()
        empty = {"type": "object", "properties": {}}
        r.register(ToolSpec("add_and_save", "a", empty, Tier.FREE, add_and_save))
        r.register(ToolSpec("danger", "d", empty, Tier.CONFIRM, danger))
        return r

    monkeypatch.setattr(chat_module, "build_default_registry", registry)


async def test_ui_events_are_recorded_in_order_and_replayed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling R11: paper_added / memory_saved / confirm_request payloads are
    kept in the agent record as ``ui_events`` (in order), and the replay
    returns them as ``agent_events`` with the pending action's live status."""
    await _bootstrap(tmp_path, monkeypatch)
    _use_ui_registry(monkeypatch)
    _script(monkeypatch, [
        _calls(("add_and_save", {})),
        _calls(("danger", {})),
        ToolTurn(content="Waiting for you."),
    ])
    app = _app()
    events = await _chat(app, "do things")
    run_id = dict(events)["session"]["run_id"]
    session_id = dict(events)["session"]["session_id"]
    confirm = dict(events)["confirm_request"]

    (raw,) = await _row("SELECT agent_record_json FROM runs WHERE id = ?", run_id)
    ui = json.loads(raw)["ui_events"]
    assert [e["event"] for e in ui] == [
        "paper_added", "memory_saved", "confirm_request"]
    assert ui[0]["data"] == {"papers_id": 7, "paper_content_id": 3, "title": "T"}
    assert ui[2]["data"]["action_id"] == confirm["action_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(f"/sessions/{session_id}/messages")
        assistant = [m for m in resp.json() if m["role"] == "assistant"][0]
        replay = assistant["agent_events"]
        assert [e["event"] for e in replay] == [
            "paper_added", "memory_saved", "confirm_request"]
        assert replay[2]["data"]["status"] == "pending"
        assert replay[2]["data"]["tool"] == "danger"

        dismissed = await client.post(
            f"/chat/actions/{confirm['action_id']}/dismiss")
        assert dismissed.status_code == 200
        resp = await client.get(f"/sessions/{session_id}/messages")
    assistant = [m for m in resp.json() if m["role"] == "assistant"][0]
    assert assistant["agent_events"][2]["data"]["status"] == "dismissed"


async def test_record_shape_is_the_same_without_ui_events(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap(tmp_path, monkeypatch)
    _script(monkeypatch, [ToolTurn(content="Hello.")])
    events = await _chat(_app(), "hi")
    run_id = dict(events)["session"]["run_id"]
    (raw,) = await _row("SELECT agent_record_json FROM runs WHERE id = ?", run_id)
    assert json.loads(raw)["ui_events"] == []


async def test_cards_survive_a_failed_turn(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap(tmp_path, monkeypatch)
    _use_ui_registry(monkeypatch)
    _script(monkeypatch, [_calls(("add_and_save", {})),
                          RuntimeError("provider exploded")])
    events = await _chat(_app(), "do things")
    assert "error" in [t for t, _ in events]
    run_id = dict(events)["session"]["run_id"]
    (raw,) = await _row("SELECT agent_record_json FROM runs WHERE id = ?", run_id)
    record = json.loads(raw)
    assert "provider exploded" in record["error"]
    assert [e["event"] for e in record["ui_events"]] == [
        "paper_added", "memory_saved"]


async def test_failed_card_save_does_not_mask_the_loop_error(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _bootstrap(tmp_path, monkeypatch)
    import paperhub.api.chat as chat_module

    _use_ui_registry(monkeypatch)

    async def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db locked")

    monkeypatch.setattr(chat_module, "_store_ui_events", broken)
    _script(monkeypatch, [_calls(("add_and_save", {})),
                          RuntimeError("provider exploded")])
    events = await _chat(_app(), "do things")
    errors = [d for t, d in events if t == "error"]
    assert errors and "provider exploded" in errors[0]["message"]
    assert "db locked" not in errors[0]["message"]
