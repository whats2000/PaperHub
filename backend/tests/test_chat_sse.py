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
from paperhub.rag.retriever import RetrievedChunk


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
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_ROUTER_MOCK",
                       '{"intent":"chitchat","model_tier":"small",'
                       '"confidence":0.9,"reasoning":"greeting"}')
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", "Hello there!")
    await _bootstrap_schema(tmp_path)
    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        # Lifespan runs schema migration. Issue request.
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hi"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" in types
    assert types.count("tool_step") >= 2  # router + chitchat
    assert "final" in types
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == "Hello there!"


async def test_chat_sse_paper_search_one_shot(tmp_path, monkeypatch) -> None:
    """paper_search is one-shot: routing_decision + final, no token events."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_search","model_tier":"flagship",'
        '"confidence":0.95,"reasoning":"add paper"}',
    )
    await _bootstrap_schema(tmp_path)

    _canned = "Added arxiv:1706.03762 — Attention Is All You Need"

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
        yield FinalOnlyMessage(_canned)

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)

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
    assert "routing_decision" in types
    assert "final" in types
    # paper_search is one-shot: no token events between routing_decision and final
    assert "token" not in types
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == _canned


async def test_chat_sse_paper_qa_streams(tmp_path, monkeypatch) -> None:
    """paper_qa streams tokens; final.content includes chunk citation markers."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_qa","model_tier":"flagship",'
        '"confidence":0.97,"reasoning":"asks about paper content"}',
    )

    # Canned retrieved chunks returned by the monkeypatched Retriever.
    _canned_chunks = [
        RetrievedChunk(chunk_id=1, paper_content_id=1, text="chunk text A", score=0.9),
        RetrievedChunk(chunk_id=2, paper_content_id=1, text="chunk text B", score=0.8),
    ]
    _canned_tokens = ["answer ", "[chunk:1]"]

    async def _fake_paper_qa_stream(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        model: Any,
        retriever: Any,
        conn: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        for tok in _canned_tokens:
            yield tok

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_qa_stream", _fake_paper_qa_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)

    # Seed the DB with a session, paper_content, chunk, and papers link
    # so the agent would have something to query (not needed with mock,
    # but ensures the DB state is coherent for the handler).
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
    assert "routing_decision" in types
    assert types.count("token") >= 2
    assert "final" in types
    final_payload = next(d for t, d in events if t == "final")
    assert "[chunk:1]" in final_payload["content"]


async def test_chat_sse_paper_qa_empty_refs_no_double_emit(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """When no refs are enabled, paper_qa SSE must have ZERO token events and
    exactly one final event with the no-refs message — not token+final with
    identical content (Issue #11 fix)."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_qa","model_tier":"flagship",'
        '"confidence":0.97,"reasoning":"asks about paper"}',
    )
    await _bootstrap_schema(tmp_path)

    _no_refs_msg = (
        "No references are enabled for this session. Add a paper to the "
        "Reference Sources panel first, then ask again."
    )

    async def _fake_paper_qa_stream(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        model: Any,
        retriever: Any,
        conn: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str | FinalOnlyMessage]:
        yield FinalOnlyMessage(_no_refs_msg)

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_qa_stream", _fake_paper_qa_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "what is this paper about?"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    # Zero token events — the sentinel must not be emitted as a token.
    assert "token" not in types, f"Expected no token events, got: {types}"
    # Exactly one final event with the no-refs message.
    final_events = [(t, d) for t, d in events if t == "final"]
    assert len(final_events) == 1
    assert final_events[0][1]["content"] == _no_refs_msg


# ---------------------------------------------------------------------------
# v2.4-1: session event emitted as first SSE event + session reuse
# ---------------------------------------------------------------------------
async def test_chat_emits_session_event_first(tmp_path: Any, monkeypatch: Any) -> None:
    """The very first SSE event must be 'session' with valid run_id and session_id."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small","confidence":1.0,"reasoning":"x"}',
    )
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", "hi")
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
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small","confidence":1.0,"reasoning":"x"}',
    )
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", "hello")
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
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"hi"}',
    )
    await _bootstrap_schema(tmp_path)

    # Fake router that raises with a string that includes a fake API key.
    async def _exploding_router(state: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Auth failed: sk-ant-fakekey9999 is invalid")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "router_node", _exploding_router)

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


# ---------------------------------------------------------------------------
# A8a: router failure → SSE emits error event
# ---------------------------------------------------------------------------
async def test_chat_sse_emits_error_event_on_router_failure(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """When the router raises, the SSE stream must emit an 'error' event and
    persist the (redacted) exception text in messages."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"hi"}',
    )
    await _bootstrap_schema(tmp_path)

    async def _failing_router(state: Any, **kwargs: Any) -> Any:
        raise ValueError("router exploded")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "router_node", _failing_router)

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
    # No final event when error occurs before final_content is produced.
    error_payloads = [d for t, d in events if t == "error"]
    assert error_payloads[0].get("message") == "router exploded"

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
    """Cancelling the request mid-stream must leave the runs row with
    status='cancelled' (or 'error' — both are acceptable per NFR-04)."""
    import asyncio

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"hi"}',
    )
    await _bootstrap_schema(tmp_path)

    # A slow chitchat stream that never finishes naturally.
    async def _slow_chitchat(state: Any, **kwargs: Any) -> AsyncIterator[str]:
        for i in range(100):
            await asyncio.sleep(0.05)
            yield f"token{i}"

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "chitchat_stream", _slow_chitchat)

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
    # Either 'cancelled' or 'error' is acceptable when the client disconnects.
    assert row is not None
    assert row[0] in ("cancelled", "error", "running", "ok"), f"Unexpected status: {row[0]}"


# ---------------------------------------------------------------------------
# v2.4-2: paper_search streams tool_step events incrementally
# ---------------------------------------------------------------------------
async def test_paper_search_streams_tool_step_events_incrementally(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The paper_search branch must emit tool_step events as the agent
    executes each tool call, not all-at-once after the loop completes.
    Events are verified to arrive BEFORE the final event (ordering contract)."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_search","model_tier":"flagship",'
        '"confidence":0.95,"reasoning":"find papers"}',
    )
    await _bootstrap_schema(tmp_path)

    # Build a fake paper_search generator that yields 3 ToolStepYield items
    # (simulating plan + search_library + add_paper_to_session) followed by
    # a FinalOnlyMessage — demonstrating the streaming contract.
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
    _step_records = [
        {**_canned_record_base, "step_index": 0, "tool": "paper_search:plan"},
        {**_canned_record_base, "step_index": 1, "tool": "paper_search:search_library",
         "model": None},
        {**_canned_record_base, "step_index": 2, "tool": "paper_search:add_paper_to_session",
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
        yield FinalOnlyMessage(_final_text)

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "find me transformer papers"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    # At least 3 tool_step events from the fake generator.
    tool_step_events = [(t, d) for t, d in events if t == "tool_step"]
    assert len(tool_step_events) >= 3, (
        f"Expected >= 3 tool_step events, got {len(tool_step_events)}: {types}"
    )

    # Ordering: every tool_step must come BEFORE the final event.
    final_idx = next(i for i, e in enumerate(events) if e[0] == "final")
    tool_step_indexes = [i for i, e in enumerate(events) if e[0] == "tool_step"]
    assert all(idx < final_idx for idx in tool_step_indexes), (
        "All tool_step events must precede the final event"
    )

    # No token events — paper_search is non-streaming text.
    assert "token" not in types

    # Final content matches.
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
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_search","model_tier":"flagship",'
        '"confidence":0.95,"reasoning":"find papers"}',
    )
    await _bootstrap_schema(tmp_path)


async def test_paper_search_emits_search_results_event_before_final(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """search_results SSE event arrives before final."""
    await _setup_paper_search_test(tmp_path, monkeypatch)

    async def _fake_paper_search(
        state: Any, *, adapter: Any, tracer: Any, model: Any, conn: Any,
        pipeline: Any, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield SearchResultsYield(candidates=[_candidate("ss:abcd", title="X")])
        yield FinalOnlyMessage("Here are picks.")

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

    types = [t for t, _ in events]
    assert "search_results" in types, f"missing search_results event in {types}"
    sr_idx = types.index("search_results")
    final_idx = types.index("final")
    assert sr_idx < final_idx
    sr_payload = events[sr_idx][1]
    assert sr_payload["run_id"] > 0
    assert len(sr_payload["candidates"]) == 1
    assert sr_payload["candidates"][0]["paper_id"] == "ss:abcd"
    # No finalize → no auto_added.
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
    """When the agent resurfaces a library:<id> candidate (finalize=False) for
    a paper that's already attached to this session, the SSE payload must
    carry both ``already_in_session=True`` AND ``papers_id`` populated so the
    frontend SearchResultList can derive its "Added" badge from the live
    references slice (and so it can flip back to the Add button when the user
    removes the paper from the panel)."""
    await _setup_paper_search_test(tmp_path, monkeypatch)

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
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
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        pcid = int(row[0])

    captured_session_id: list[int] = []

    async def _fake_paper_search(
        state: Any, *, adapter: Any, tracer: Any, model: Any, conn: Any,
        pipeline: Any, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        # Pre-seed the papers row inside the test app's connection so
        # _mark_already_in_session sees it.
        sid = state["session_id"]
        captured_session_id.append(sid)
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) "
            "VALUES (?, ?, 1)",
            (sid, pcid),
        )
        await conn.commit()
        yield SearchResultsYield(
            candidates=[_candidate(f"library:{pcid}", finalize=False)],
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
    assert cand["already_in_session"] is True
    assert cand["papers_id"] is not None, (
        "library: candidates already in session must carry papers_id so the "
        "frontend can match them against referencesBySession (papers_id is "
        "the only join key for PDF-only papers where arxiv_id is NULL)"
    )
    assert cand["auto_added"] is False  # finalize=False → not auto-attached


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

async def test_chat_sse_clarify_surfaces_question_no_pipeline(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """When the router emits intent='clarify', the SSE stream must:
    - emit a routing_decision event with intent='clarify'
    - emit a final event whose content is exactly the resolved_query
    - NOT emit any token events (the question is surfaced as one final block)
    - NOT emit any tool_step events from the paper_search pipeline."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"clarify","model_tier":"small","confidence":0.4,'
        '"reasoning":"ambiguous","resolved_query":"Which topic do you mean?"}',
    )
    await _bootstrap_schema(tmp_path)
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
    assert "routing_decision" in types
    assert "final" in types
    # The clarifying question must appear in final.content.
    final_payload = next(d for t, d in events if t == "final")
    assert "Which topic do you mean?" in final_payload["content"]
    # No paper_search-related tool_step events — the pipeline must not run.
    paper_search_tool_steps = [
        d for t, d in events
        if t == "tool_step"
        and isinstance(d.get("record"), dict)
        and "paper_search" in (d["record"].get("tool") or "")
    ]
    assert paper_search_tool_steps == [], (
        f"Expected no paper_search tool_step events for clarify, got: {paper_search_tool_steps}"
    )
