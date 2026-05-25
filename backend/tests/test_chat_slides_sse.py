"""Tests for the `slides` intent SSE path (Task 10, Plan F).

The ``report_stream`` shim is monkeypatched with a canned generator so the
test does NOT require a real LLM, LaTeX compiler, or DB deck state.  The
pattern mirrors ``test_chat_sse.py`` exactly.
"""
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paperhub.agents.research import FinalOnlyMessage, ToolStepYield
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema


class _FakeMcpRegistry:
    async def aggregate_tool_schemas(self) -> list[Any]:
        return []

    async def has_tool(self, name: str) -> bool:
        return False

    async def call(self, name: str, args: dict[str, Any]) -> Any:  # pragma: no cover
        raise RuntimeError(f"_FakeMcpRegistry.call unexpectedly called for {name!r}")


def _wire_test_app() -> FastAPI:
    app = create_app()
    app.state.mcp_registry = _FakeMcpRegistry()
    return app


async def _bootstrap_schema(tmp_path: Any) -> None:
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)


async def _consume_sse(stream: AsyncIterator[bytes]) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    import json

    events: list[tuple[str, dict]] = []  # type: ignore[type-arg]
    buf = ""
    async for chunk in stream:
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


# ---------------------------------------------------------------------------
# Task 10: slides intent → deck SSE event
# ---------------------------------------------------------------------------

async def test_chat_sse_slides_emits_deck_event(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """slides intent must:
    - route through report_stream (not the else-stub)
    - emit a 'deck' SSE event whose data has page_count == 3
    - emit a 'final' event whose content matches the FinalOnlyMessage
    - NOT emit token events (deck is one-shot like paper_search)
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"slides","model_tier":"flagship",'
        '"confidence":0.95,"reasoning":"user wants slides"}',
    )
    await _bootstrap_schema(tmp_path)

    _canned_deck = {
        "deck_id": 1,
        "session_id": 1,
        "page_count": 3,
        "title": "Test Deck",
        "status": "ok",
        "contributing_papers": [],
        "has_notes": False,
    }
    _canned_final = "Generated a 3-slide deck."

    async def _fake_report_stream(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        conn: Any,
        retriever: Any,
        settings: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        import paperhub.api.chat as chat_module

        yield chat_module.DeckYield(deck=_canned_deck)
        yield FinalOnlyMessage(_canned_final)

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "report_stream", _fake_report_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "make me a slide deck"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "routing_decision" in types, f"missing routing_decision in {types}"
    assert "deck" in types, f"missing deck event in {types}"
    assert "final" in types, f"missing final event in {types}"

    # deck event must carry page_count == 3
    deck_payload = next(d for t, d in events if t == "deck")
    assert deck_payload["page_count"] == 3, (
        f"Expected page_count == 3 in deck event, got: {deck_payload}"
    )

    # final content must match the FinalOnlyMessage
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == _canned_final, (
        f"Expected final.content == {_canned_final!r}, got: {final_payload['content']!r}"
    )

    # deck is one-shot — no token events
    assert "token" not in types, (
        f"Expected no token events for slides intent, got: {types}"
    )

    # deck event must arrive before final
    deck_idx = types.index("deck")
    final_idx = types.index("final")
    assert deck_idx < final_idx, "deck event must precede final event"


async def test_chat_sse_slides_threads_current_view_page(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """The ChatRequest.current_view_page must reach the AgentState that flows
    into report_stream so the deck-command classifier can resolve "edit this
    slide" to the on-screen page."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"slides","model_tier":"flagship",'
        '"confidence":0.95,"reasoning":"user wants slides"}',
    )
    await _bootstrap_schema(tmp_path)

    captured: dict[str, Any] = {}

    async def _fake_report_stream(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        conn: Any,
        retriever: Any,
        settings: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        captured["state"] = state
        yield FinalOnlyMessage("done")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "report_stream", _fake_report_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={
                "session_id": None,
                "user_message": "edit this slide",
                "current_view_page": 4,
            },
        ) as response:
            assert response.status_code == 200
            await _consume_sse(response.aiter_bytes())

    assert captured["state"]["current_view_page"] == 4, (
        f"Expected current_view_page == 4 in AgentState, got: {captured['state']}"
    )


async def test_chat_sse_slides_tool_step_events_forwarded(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """tool_step events from report_stream must be forwarded to the SSE stream
    before the final event (mirrors paper_search streaming contract)."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"slides","model_tier":"flagship",'
        '"confidence":0.95,"reasoning":"user wants slides"}',
    )
    await _bootstrap_schema(tmp_path)

    _canned_record: dict[str, Any] = {
        "run_id": 1,
        "branch": "",
        "step_index": 0,
        "parent_step": None,
        "agent": "report",
        "tool": "report:plan",
        "model": "gemini/gemini-2.5-pro",
        "args_redacted_json": None,
        "result_summary_json": None,
        "latency_ms": 10,
        "token_in": None,
        "token_out": None,
        "status": "ok",
        "error": None,
    }

    async def _fake_report_stream(
        state: Any,
        *,
        adapter: Any,
        tracer: Any,
        conn: Any,
        retriever: Any,
        settings: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        import paperhub.api.chat as chat_module

        yield ToolStepYield(record=_canned_record)
        yield chat_module.DeckYield(deck={"deck_id": 1, "session_id": 1, "page_count": 2,
                                          "title": "T", "status": "ok",
                                          "contributing_papers": [], "has_notes": False})
        yield FinalOnlyMessage("Generated a 2-slide deck.")

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "report_stream", _fake_report_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "make slides"},
        ) as response:
            assert response.status_code == 200
            events = await _consume_sse(response.aiter_bytes())

    types = [t for t, _ in events]
    assert "tool_step" in types, f"Expected tool_step events, got: {types}"
    assert "deck" in types, f"Expected deck event, got: {types}"
    assert "final" in types, f"Expected final event, got: {types}"

    # All tool_step events must precede final
    final_idx = types.index("final")
    tool_step_indexes = [i for i, t in enumerate(types) if t == "tool_step"]
    assert all(idx < final_idx for idx in tool_step_indexes), (
        "All tool_step events must precede the final event"
    )
