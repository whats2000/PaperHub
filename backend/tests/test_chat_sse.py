import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paperhub.agents.research import (
    FinalOnlyMessage,
    SearchCandidate,
    SearchResultsYield,
    ToolStepYield,
)
from paperhub.agents.research_tools import AddResult, NoIngestibleSourceError
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema
from tests.agent_script import answer, calls, pin_pipeline_turn, script_agent


class _FakeMcpRegistry:
    """Minimal stand-in for :class:`MCPRegistry` for tests that don't actually
    exercise the agent's MCP dispatch path (every paper_search/paper_qa case
    in this file monkeypatches the agent with a canned generator).
    """

    async def aggregate_tool_schemas(self) -> list[Any]:
        return []

    async def has_tool(self, name: str) -> bool:
        return False

    async def call(self, name: str, args: dict[str, Any]) -> Any:  # pragma: no cover
        raise RuntimeError(
            f"_FakeMcpRegistry.call invoked unexpectedly for {name!r} — test "
            "should monkeypatch paper_search before reaching dispatch",
        )


def _wire_test_app() -> FastAPI:
    """Build a test app with ``app.state.mcp_registry`` pre-populated.

    ASGITransport does NOT trigger the FastAPI lifespan, so the real
    registry the lifespan installs is never created. Tests monkeypatch
    the agent itself so the registry is never dispatched through; the
    stub exists only so ``request.app.state.mcp_registry`` resolves.
    """
    app = create_app()
    app.state.mcp_registry = _FakeMcpRegistry()
    return app


async def _bootstrap_schema(tmp_path: Any) -> None:
    """Seed an empty schema into the workspace DB.

    ASGITransport does not trigger ASGI lifespan, so tests that don't
    pre-populate the DB need to call this before the AsyncClient context.
    """
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)


async def _consume_sse(stream: AsyncIterator[bytes]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    buf = ""
    async for chunk in stream:
        # Normalise CRLF → LF so the block-splitter works regardless of
        # whether sse_starlette uses \r\n or \n line endings.
        buf += chunk.decode("utf-8").replace("\r\n", "\n")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event_type = ""
            data = ""
            for line in block.splitlines():
                if line.startswith("event: "):
                    event_type = line[len("event: "):]
                elif line.startswith("data: "):
                    data = line[len("data: "):]
            if event_type:
                events.append((event_type, json.loads(data) if data else {}))
    return events


async def test_chat_sse_chitchat_round_trip(tmp_path, monkeypatch) -> None:
    """H4: a plain answer turn — the agent answers without tools."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    script_agent(monkeypatch, [answer("Hello there!")])
    await _bootstrap_schema(tmp_path)
    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hi"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" not in types
    assert types.count("tool_step") >= 1  # the agent:step row
    assert types[-2:] == ["agent_summary", "final"]
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == "Hello there!"


async def test_chat_sse_paper_search_one_shot(tmp_path, monkeypatch) -> None:
    """H4: the agent calls search_papers (the paper_search pipeline), then
    answers; the answer streams as tokens and is the final content."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)

    _canned = "Added arxiv:1706.03762 — Attention Is All You Need"
    searched: list[str] = []

    async def _fake_paper_search(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        model: Any,
        conn: Any,
        pipeline: Any,
        **kwargs: Any,
    ) -> AsyncIterator[FinalOnlyMessage]:
        searched.append(state["effective_query"])
        yield FinalOnlyMessage("")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    script_agent(monkeypatch, [
        calls(("search_papers", {"query": "attention is all you need"})),
        answer(_canned),
    ])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "add attention is all you need"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" not in types
    assert "final" in types
    assert searched == ["attention is all you need"]
    tools = [d["record"]["tool"] for t, d in events if t == "tool_step"]
    assert "tool:search_papers" in tools
    token_text = "".join(d["text"] for t, d in events if t == "token")
    assert token_text == _canned
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == _canned


async def test_chat_sse_paper_qa_streams(tmp_path, monkeypatch) -> None:
    """H4: the agent reads a chunk, then streams an answer citing it; the
    citation survives validation because the chunk was read."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    script_agent(monkeypatch, [
        calls(("read_chunks", {"ids": [1]})),
        answer("answer [chunk:1]"),
    ])

    app = _wire_test_app()
    transport = ASGITransport(app=app)

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as seed_conn:
        await apply_schema(seed_conn)
        await seed_conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await seed_conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "arxiv:1706.03762", "arxiv", "1706.03762",
                "Attention Is All You Need", "[]", 2017,
                "Transformer architecture.", "/tmp/s.tex", "/tmp", "/tmp/s.html",
            ),
        )
        await seed_conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (1, 'abstract', 0, 50, 'chunk text A')",
        )
        await seed_conn.execute(
            "INSERT INTO papers (session_id, paper_content_id) VALUES (1, 1)",
        )
        await seed_conn.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": 1, "user_message": "what is the attention mechanism?"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" not in types
    assert types.count("token") >= 2
    assert "final" in types
    final_payload = next(d for t, d in events if t == "final")
    assert "[chunk:1]" in final_payload["content"]
    summary = next(d for t, d in events if t == "agent_summary")
    assert summary["papers_read"] == 1


async def test_chat_sse_paper_qa_empty_refs_no_double_emit(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """H4 analogue of the Issue #11 fix: when the agent produces no answer
    (two empty turns), the fallback sentence arrives as exactly ONE token and
    one final with the same text — never duplicated."""
    from paperhub.agents.core.agent import EMPTY_ANSWER

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)
    script_agent(monkeypatch, [answer(""), answer("")])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "what is this paper about?"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    token_events = [d for t, d in events if t == "token"]
    assert [d["text"] for d in token_events] == [EMPTY_ANSWER]
    final_events = [(t, d) for t, d in events if t == "final"]
    assert len(final_events) == 1
    assert final_events[0][1]["content"] == EMPTY_ANSWER


# ---------------------------------------------------------------------------
# v2.4-1: session event emitted as first SSE event + session reuse
# ---------------------------------------------------------------------------
async def test_chat_emits_session_event_first(tmp_path: Any, monkeypatch: Any) -> None:
    """The very first SSE event must be 'session' with valid run_id and session_id."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    script_agent(monkeypatch, [answer("hi")])
    await _bootstrap_schema(tmp_path)
    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hi"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    assert events, "Expected at least one SSE event"
    first_type, first_payload = events[0]
    assert first_type == "session", (
        f"Expected first event to be 'session', got '{first_type}'"
    )
    assert isinstance(first_payload.get("session_id"), int)
    assert first_payload["session_id"] > 0
    assert isinstance(first_payload.get("run_id"), int)
    assert first_payload["run_id"] > 0


async def test_chat_reuses_session_when_session_id_provided(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Two POST turns with the same session_id must land in the same chat_sessions row."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    script_agent(monkeypatch, [answer("hello"), answer("hello again")])
    await _bootstrap_schema(tmp_path)
    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First turn: no session_id → backend creates one
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "first"},
        ) as response:
            assert response.status_code == 200
            events1 = await _consume_sse(response.aiter_bytes())

        sess_payload = dict(events1[0][1])
        backend_session_id = sess_payload["session_id"]
        assert isinstance(backend_session_id, int)

        # Second turn: pass the learned session_id
        async with client.stream(
            "POST", "/chat",
            json={"session_id": backend_session_id, "user_message": "second"},
        ) as response:
            assert response.status_code == 200
            events2 = await _consume_sse(response.aiter_bytes())

    sess_payload2 = dict(events2[0][1])
    assert sess_payload2["session_id"] == backend_session_id

    # Verify both turns landed in the same chat_sessions row
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?",
        (backend_session_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    # Each chat turn writes 1 user + 1 assistant row = 4 rows total for 2 turns
    assert row[0] >= 4


# ---------------------------------------------------------------------------
# A7: exception strings must be redacted before persistence / SSE emission
# ---------------------------------------------------------------------------
async def test_chat_sse_exception_text_is_redacted(tmp_path: Any, monkeypatch: Any) -> None:
    """Exceptions containing API keys must be redacted in the persisted
    messages row and in the SSE error event (A7)."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)
    # The agent's model call raises with a string that includes a fake key.
    script_agent(monkeypatch, [
        RuntimeError("Auth failed: sk-ant-fakekey9999 is invalid"),
    ])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hi"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    # SSE error event must have redacted text.
    error_events = [(t, d) for t, d in events if t == "error"]
    assert error_events, "Expected an SSE error event"
    err_msg = error_events[0][1].get("message", "")
    assert "fakekey9999" not in err_msg
    assert "<redacted:anthropic>" in err_msg

    # messages row in DB must also have redacted text.
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT content FROM messages WHERE role = 'assistant'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "fakekey9999" not in row[0]
    assert "<redacted:anthropic>" in row[0]


async def test_chat_sse_emits_error_event_on_agent_failure(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """When the agent's model call raises, the SSE stream must emit an
    'error' event and persist the (redacted) exception text in messages."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)
    script_agent(monkeypatch, [ValueError("agent exploded")])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hello"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "error" in types, f"Expected error event, got: {types}"
    assert "final" not in types
    error_payloads = [d for t, d in events if t == "error"]
    assert error_payloads[0].get("message") == "agent exploded"

    # runs row must be status='error'.
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:  # noqa: SIM117
        async with conn.execute("SELECT status FROM runs") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == "error"


# ---------------------------------------------------------------------------
# A8b: mid-stream cancellation → runs row finalised as 'cancelled'
# ---------------------------------------------------------------------------
async def test_chat_sse_cancellation_finalises_run(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Disconnecting mid-stream must leave the runs row in a legal status
    (NFR-04); the run keeps going (FR-15)."""
    import asyncio

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)
    # A slow answer stream.
    script_agent(monkeypatch, [answer("token " * 50)], token_delay=0.05)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hello"},
        ) as response:
            assert response.status_code == 200
            # Read a few bytes then break out of the stream (simulates disconnect).
            byte_count = 0
            async for chunk in response.aiter_bytes():
                byte_count += len(chunk)
                if byte_count > 50:
                    break

    # Give the server coroutine a moment to clean up.
    await asyncio.sleep(0.2)

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:  # noqa: SIM117
        async with conn.execute("SELECT status FROM runs") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] in ("cancelled", "error", "running", "ok"), f"Unexpected status: {row[0]}"


# ---------------------------------------------------------------------------
# v2.4-2: paper_search streams tool_step events incrementally
# ---------------------------------------------------------------------------
async def test_paper_search_streams_tool_step_events_incrementally(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The paper_search pipeline's per-stage rows reach the stream as
    tool_step events (once each) before the final event, alongside the
    agent's own rows."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)

    _canned_record_base: dict[str, Any] = {
        "run_id": 1,
        "branch": "",
        "parent_step": None,
        "agent": "research",
        "tool": "paper_search:plan",
        "model": "gemini/gemini-2.5-flash",
        "args_redacted_json": None,
        "result_summary_json": None,
        "latency_ms": 10,
        "token_in": None,
        "token_out": None,
        "status": "ok",
        "error": None,
    }
    # High indexes so they cannot collide with the tracer's own rows.
    _step_records = [
        {**_canned_record_base, "step_index": 100, "tool": "paper_search:plan"},
        {**_canned_record_base, "step_index": 101, "tool": "paper_search:search_library",
         "model": None},
        {**_canned_record_base, "step_index": 102, "tool": "paper_search:add_paper_to_session",
         "model": None},
    ]
    _final_text = "Added 'Attention Is All You Need' from your library."

    async def _fake_paper_search(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        model: Any,
        conn: Any,
        pipeline: Any,
        **kwargs: Any,
    ) -> AsyncIterator[ToolStepYield | FinalOnlyMessage]:
        for rec in _step_records:
            yield ToolStepYield(record=rec)
        yield FinalOnlyMessage("")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    script_agent(monkeypatch, [
        calls(("search_papers", {"query": "transformer"})),
        answer(_final_text),
    ])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "find me transformer papers"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    tool_steps = [d["record"] for t, d in events if t == "tool_step"]
    tools = [r["tool"] for r in tool_steps]
    for rec in _step_records:
        assert tools.count(rec["tool"]) == 1, tools
    assert "tool:search_papers" in tools
    assert "agent:step" in tools
    # live-only beats (negative index) are not trace rows
    indexes = [r["step_index"] for r in tool_steps if r["step_index"] >= 0]
    assert len(indexes) == len(set(indexes))

    # Ordering: every tool_step must come BEFORE the final event.
    final_idx = next(i for i, e in enumerate(events) if e[0] == "final")
    tool_step_indexes = [i for i, e in enumerate(events) if e[0] == "tool_step"]
    assert all(idx < final_idx for idx in tool_step_indexes), (
        "All tool_step events must precede the final event"
    )

    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == _final_text


# ---------------------------------------------------------------------------
# v2.4-5: search_results SSE event, finalize cap, auto-attach
# ---------------------------------------------------------------------------


def _candidate(
    paper_id: str,
    *,
    title: str = "T",
    finalize: bool = False,
) -> SearchCandidate:
    return SearchCandidate(
        paper_id=paper_id,
        title=title,
        authors=[],
        year=2024,
        abstract="abs",
        arxiv_id=None,
        has_open_pdf=False,
        reason="r",
        finalize=finalize,
    )


async def _setup_paper_search_test(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """Finalize-cap / auto-attach behaviour exists only in the pre-H4
    paper_search pipeline turn (the agent attaches with add_paper), so these
    tests pin POST /chat to ``run_pipeline_turn``."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_search",'
        '"confidence":0.95,"reasoning":"find papers"}',
    )
    pin_pipeline_turn(monkeypatch)
    await _bootstrap_schema(tmp_path)


async def _agent_search_turn(
    monkeypatch: Any, candidates: list[SearchCandidate],
    *, message: str = "find papers",
) -> list[tuple[str, dict]]:
    """One agent turn: search_papers (fake pipeline yields ``candidates``),
    then an answer."""

    async def _fake_paper_search(state: Any, **kwargs: Any) -> AsyncIterator[Any]:
        yield SearchResultsYield(candidates=list(candidates))
        yield FinalOnlyMessage("")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    script_agent(monkeypatch, [
        calls(("search_papers", {"query": message})),
        answer("Here are picks."),
    ])
    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": message},
        ) as response:
            assert response.status_code == 200
            return await _consume_sse(response.aiter_bytes())


async def test_paper_search_emits_search_results_event_before_final(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """search_results SSE event arrives before final."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)
    events = await _agent_search_turn(
        monkeypatch, [_candidate("ss:abcd", title="X")])

    types = [t for t, _ in events]
    assert "search_results" in types, f"missing search_results event in {types}"
    sr_idx = types.index("search_results")
    final_idx = types.index("final")
    assert sr_idx < final_idx
    sr_payload = events[sr_idx][1]
    assert sr_payload["run_id"] > 0
    assert len(sr_payload["candidates"]) == 1
    assert sr_payload["candidates"][0]["paper_id"] == "ss:abcd"
    # The agent never auto-attaches a search hit.
    assert sr_payload["candidates"][0]["auto_added"] is False


async def test_paper_search_caps_finalize_at_2_when_agent_marks_three(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """3 finalize:true candidates → exactly 2 keep finalize=True in the
    emitted payload (and only 2 are auto_added)."""
    await _setup_paper_search_test(tmp_path, monkeypatch)

    async def _fake_paper_search(
        state: Any, *, adapter: Any, tracer: Any, model: Any, conn: Any,
        pipeline: Any, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield SearchResultsYield(
            candidates=[
                _candidate("ss:one", finalize=True),
                _candidate("ss:two", finalize=True),
                _candidate("ss:three", finalize=True),
                _candidate("ss:four", finalize=False),
            ],
        )
        yield FinalOnlyMessage("picks")

    attached: list[str] = []

    async def _fake_dispatch(paper_id: str, **kwargs: Any) -> AddResult:
        attached.append(paper_id)
        return AddResult(
            paper_content_id=99, papers_id=len(attached), cache_hit=False,
            title="T",
        )

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    monkeypatch.setattr(
        chat_module, "add_paper_to_session_dispatch", _fake_dispatch,
    )

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "find papers"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    sr_payload = next(d for t, d in events if t == "search_results")
    finalize_count = sum(1 for c in sr_payload["candidates"] if c["finalize"])
    assert finalize_count == 2, (
        f"Expected 2 finalize=True after cap, got {finalize_count}"
    )
    auto_added_count = sum(1 for c in sr_payload["candidates"] if c["auto_added"])
    assert auto_added_count == 2
    # The first two (by agent order) should be the kept finalize ones.
    assert sr_payload["candidates"][0]["finalize"] is True
    assert sr_payload["candidates"][1]["finalize"] is True
    assert sr_payload["candidates"][2]["finalize"] is False
    assert sr_payload["candidates"][3]["finalize"] is False
    # Only 2 dispatcher calls.
    assert attached == ["ss:one", "ss:two"]


async def test_paper_search_auto_attaches_finalize_marked_candidates_and_populates_papers_id(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """A finalize=True library:<id> candidate ends up with auto_added=True
    + papers_id populated; a corresponding papers row exists."""
    await _setup_paper_search_test(tmp_path, monkeypatch)

    # Seed a paper_content row so library: can be attached.
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "arxiv:1706.03762", "arxiv", "1706.03762",
                "Attention Is All You Need", "[]", 2017,
                "abs", "/tmp/s.tex", "/tmp", "/tmp/s.html",
            ),
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        pcid = int(row[0])

    async def _fake_paper_search(
        state: Any, *, adapter: Any, tracer: Any, model: Any, conn: Any,
        pipeline: Any, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield SearchResultsYield(
            candidates=[
                _candidate(f"library:{pcid}", finalize=True),
            ],
        )
        yield FinalOnlyMessage("picks")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "find papers"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    sr_payload = next(d for t, d in events if t == "search_results")
    cand = sr_payload["candidates"][0]
    assert cand["auto_added"] is True
    assert cand["papers_id"] is not None
    assert cand["error"] is None

    # Verify papers row exists in DB.
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT id FROM papers WHERE paper_content_id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None


async def test_paper_search_library_already_in_session_populates_papers_id(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """When search_papers resurfaces a library:<id> candidate for a paper
    that's already attached to this session, the SSE payload must carry both
    ``already_in_session=True`` AND ``papers_id`` populated so the frontend
    SearchResultList can derive its "Added" badge from the live references
    slice (and flip back to the Add button when the paper is removed)."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, sha256, title, authors_json, year, "
            "abstract, source_path, source_dir_path, html_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sha256:abc123", "pdf_upload", None, "abc123",
                "Some PDF Paper", "[]", 2020,
                "abs", "/tmp/s.pdf", "/tmp", "/tmp/s.html",
            ),
        )
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        pcid = int(row[0])
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) "
            "VALUES (1, ?, 1)", (pcid,),
        )
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            prow = await cur.fetchone()
        assert prow is not None
        await conn.commit()

    async def _fake_paper_search(state: Any, **kwargs: Any) -> AsyncIterator[Any]:
        yield SearchResultsYield(
            candidates=[_candidate(f"library:{pcid}", finalize=False)],
        )
        yield FinalOnlyMessage("")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    script_agent(monkeypatch, [
        calls(("search_papers", {"query": "pdf paper"})),
        answer("It is already here."),
    ])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": 1, "user_message": "find papers"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    sr_payload = next(d for t, d in events if t == "search_results")
    cand = sr_payload["candidates"][0]
    assert cand["already_in_session"] is True
    assert cand["papers_id"] == int(prow[0]), (
        "library: candidates already in session must carry papers_id so the "
        "frontend can match them against referencesBySession (papers_id is "
        "the only join key for PDF-only papers where arxiv_id is NULL)"
    )
    assert cand["auto_added"] is False


async def test_paper_search_no_papers_row_for_suggested_only_candidates(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """finalize:false ss:<id> → no INSERT into papers, dispatcher not called."""
    await _setup_paper_search_test(tmp_path, monkeypatch)

    async def _fake_paper_search(
        state: Any, *, adapter: Any, tracer: Any, model: Any, conn: Any,
        pipeline: Any, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield SearchResultsYield(
            candidates=[_candidate("ss:suggested", finalize=False)],
        )
        yield FinalOnlyMessage("picks")

    dispatch_calls: list[str] = []

    async def _fake_dispatch(paper_id: str, **kwargs: Any) -> AddResult:
        dispatch_calls.append(paper_id)
        return AddResult(
            paper_content_id=1, papers_id=1, cache_hit=False, title="x",
        )

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    monkeypatch.setattr(
        chat_module, "add_paper_to_session_dispatch", _fake_dispatch,
    )

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "find papers"},
        ) as response:
            assert response.status_code == 200
            await _consume_sse(response.aiter_bytes())

    assert dispatch_calls == []
    # And no papers row.
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT COUNT(*) FROM papers",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 0


async def test_paper_search_finalize_no_ingestible_source_marks_error_and_continues(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """finalize:true with NoIngestibleSourceError → auto_added=false, error
    field set; other suggested-only candidates still emitted."""
    await _setup_paper_search_test(tmp_path, monkeypatch)

    async def _fake_paper_search(
        state: Any, *, adapter: Any, tracer: Any, model: Any, conn: Any,
        pipeline: Any, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield SearchResultsYield(
            candidates=[
                _candidate("ss:nosrc", title="No Source", finalize=True),
                _candidate("ss:also", title="Also", finalize=False),
            ],
        )
        yield FinalOnlyMessage("picks")

    async def _fake_dispatch(paper_id: str, **kwargs: Any) -> AddResult:
        raise NoIngestibleSourceError(paper_id=paper_id, title="No Source")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    monkeypatch.setattr(
        chat_module, "add_paper_to_session_dispatch", _fake_dispatch,
    )

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "find papers"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    sr_payload = next(d for t, d in events if t == "search_results")
    assert len(sr_payload["candidates"]) == 2
    nosrc = sr_payload["candidates"][0]
    assert nosrc["auto_added"] is False
    assert nosrc["error"] == "no_ingestible_source"
    also = sr_payload["candidates"][1]
    assert also["auto_added"] is False
    assert also["error"] is None


# ---------------------------------------------------------------------------
# v2.11-6: clarify intent — router surfaces a clarifying question
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# v2.12-6: paper_suggest intent routes through the paper_search pipeline
# ---------------------------------------------------------------------------

async def test_chat_sse_paper_suggest_runs_pipeline(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """search_papers with mode='suggest' runs the paper_search pipeline in
    suggest mode and emits a search_results SSE event before final."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)
    suggest_flags: list[bool] = []

    async def _fake_paper_search(
        state: Any, *, adapter: Any, tracer: Any, model: Any, conn: Any,
        pipeline: Any, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        suggest_flags.append(bool(kwargs.get("suggest")))
        yield SearchResultsYield(candidates=[_candidate("ss:rag123", title="RAG Paper")])
        yield FinalOnlyMessage("")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)
    script_agent(monkeypatch, [
        calls(("search_papers", {"query": "retrieval augmented generation",
                                 "mode": "suggest"})),
        answer("Here are some recommended papers on RAG."),
    ])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "recommend papers on RAG"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert suggest_flags == [True]
    assert "search_results" in types, (
        f"Expected search_results event (pipeline ran), got: {types}"
    )
    sr_idx = types.index("search_results")
    final_idx = types.index("final")
    assert sr_idx < final_idx


# ---------------------------------------------------------------------------
# Task 7: library_stats intent — SQL Agent streamed via chat dispatch
# ---------------------------------------------------------------------------

async def test_chat_sse_library_stats_streams_tokens(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """The agent's query_library tool runs the SQL Agent; its answer reaches
    the model as the tool result, and the agent's answer streams as tokens
    and is the final content."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)

    _canned_tokens = ["You have ", "3 papers."]

    async def _fake_sql_agent_stream(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        registry: Any,
        planner_model: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        for tok in _canned_tokens:
            yield tok

    import paperhub.agents.core.action_tools as action_tools

    monkeypatch.setattr(action_tools, "sql_agent_stream", _fake_sql_agent_stream)
    seen = script_agent(monkeypatch, [
        calls(("query_library", {"question": "how many papers do I have?"})),
        answer("You have 3 papers."),
    ])

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client, client.stream(
        "POST", "/chat",
        json={"session_id": None, "user_message": "how many papers do I have?"},
    ) as response:
        assert response.status_code == 200
        events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" not in types
    assert "token" in types, f"Expected token events, got: {types}"
    assert "final" in types
    tools = [d["record"]["tool"] for t, d in events if t == "tool_step"]
    assert "tool:query_library" in tools
    # The SQL Agent's answer is what the model saw as the tool result.
    tool_msgs = [m for m in seen[1]["messages"] if m.get("role") == "tool"]
    assert "You have 3 papers." in tool_msgs[-1]["content"]

    token_text = "".join(d.get("text", "") for t, d in events if t == "token")
    assert "3 papers." in token_text

    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == "You have 3 papers."


async def test_chat_sse_memory_intent_persists_row_and_emits_final(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """The agent's remember tool persists a memories row and emits a
    memory_saved event; the agent's confirmation is the final content."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))

    _confirmation = "Noted — I will remember that."

    async def _fake_add_memory(conn: Any, *, session_id: int, content: str,
                               scope: str, **kwargs: Any) -> int:
        await conn.execute(
            "INSERT INTO memories (scope, session_id, content) VALUES (?, ?, ?)",
            (scope, session_id, content),
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        return int(row[0])

    import paperhub.agents.core.action_tools as action_tools

    monkeypatch.setattr(action_tools, "add_memory_with_supersede", _fake_add_memory)
    script_agent(monkeypatch, [
        calls(("remember", {"fact": "prefers concise answers", "scope": "session"})),
        answer(_confirmation),
    ])

    await _bootstrap_schema(tmp_path)
    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "remember that I prefer concise answers"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" not in types
    assert "memory_saved" in types
    saved = next(d for t, d in events if t == "memory_saved")
    assert saved["content"] == "prefers concise answers"
    assert "token" in types, f"Expected token event for memory confirmation, got: {types}"

    token_text = "".join(d.get("text", "") for t, d in events if t == "token")
    assert _confirmation in token_text, (
        f"Expected confirmation text in token stream, got: {token_text!r}"
    )

    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == _confirmation, (
        f"Expected final.content == confirmation, got: {final_payload['content']!r}"
    )

    # Verify a memories row was persisted.
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT content FROM memories",
    ) as cur:
        rows = await cur.fetchall()
    assert rows, "Expected at least one memories row after remember"
    assert any("concise" in r[0] for r in rows), (
        f"Expected 'concise' in memories content, got: {rows}"
    )


async def test_chat_sse_clarify_surfaces_question_no_pipeline(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """An unresolvable turn: the agent asks a clarifying question itself —
    it streams as tokens, is the final content, and no search ran."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _bootstrap_schema(tmp_path)
    script_agent(monkeypatch, [answer("Which topic do you mean?")])
    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "推薦幾篇"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" not in types
    assert "final" in types
    token_text = "".join(d.get("text", "") for t, d in events if t == "token")
    assert "Which topic do you mean?" in token_text
    final_payload = next(d for t, d in events if t == "final")
    assert "Which topic do you mean?" in final_payload["content"]
    search_steps = [
        d for t, d in events
        if t == "tool_step"
        and isinstance(d.get("record"), dict)
        and "search" in (d["record"].get("tool") or "")
    ]
    assert search_steps == []


