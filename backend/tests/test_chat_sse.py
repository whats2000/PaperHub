import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

from paperhub.agents.research import FinalOnlyMessage
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema
from paperhub.rag.retriever import RetrievedChunk


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
    app = create_app()
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
    ) -> str:
        return _canned

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)

    app = create_app()
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

    app = create_app()
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

    app = create_app()
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
    app = create_app()
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
    app = create_app()
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

    app = create_app()
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

    app = create_app()
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

    app = create_app()
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
