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
