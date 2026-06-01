"""F4.5 wiring tests for the chat endpoint's ``slides`` intent branch.

Two behaviours are pinned here:

1. A deterministic style-command message (``reset slide style``) MUST
   short-circuit BEFORE the Report subgraph (``report_stream``) is invoked,
   emit a plain-text confirmation reply, and finalise normally.
2. A non-style-command slides message (``generate slides for these papers``)
   MUST fall through to ``report_stream`` — the slide pipeline is the
   default path on the slides intent.

The tests drive the real FastAPI app via ``httpx.ASGITransport`` (matching
``test_chat_mcp_headers.py``'s pattern) and assert by monkeypatching
``paperhub.api.chat.report_stream`` to detect whether it was invoked.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paperhub.agents.research import FinalOnlyMessage
from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema


class _FakeMcpRegistry:
    async def aggregate_tool_schemas(self) -> list[Any]:
        return []

    async def has_tool(self, name: str) -> bool:
        return False

    async def call(self, name: str, args: dict[str, Any]) -> Any:  # pragma: no cover
        raise RuntimeError("not used in this test")


def _wire_test_app() -> FastAPI:
    app = create_app()
    app.state.mcp_registry = _FakeMcpRegistry()
    return app


async def _bootstrap_schema() -> None:
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)


async def _consume_sse(stream: AsyncIterator[bytes]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
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


def _route_slides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the router to land on the slides intent (no real LLM call)."""
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"slides","model_tier":"small",'
        '"confidence":0.95,"reasoning":"slide intent"}',
    )


@pytest.mark.asyncio
async def test_style_intercept_skips_report_graph(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reset slide style`` short-circuits BEFORE ``report_stream`` runs."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    _route_slides(monkeypatch)
    await _bootstrap_schema()

    called: dict[str, int] = {"report_stream": 0}

    async def _fake_report_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        called["report_stream"] += 1
        yield FinalOnlyMessage("should not see this")

    from paperhub.api import chat as chat_module
    monkeypatch.setattr(chat_module, "report_stream", _fake_report_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "reset slide style"},
        ) as response:
            events = await _consume_sse(response.aiter_bytes())

    # The slide pipeline must NOT have been called — the intercept fired.
    assert called["report_stream"] == 0, (
        "report_stream was invoked despite a deterministic style-command match"
    )

    # The final SSE message must carry the intercept's confirmation reply.
    final_evts = [e for e in events if e[0] == "final"]
    assert final_evts, events
    final_text = final_evts[-1][1]["content"]
    assert "default" in final_text.lower() or "reset" in final_text.lower(), (
        f"expected a reset confirmation reply; got: {final_text!r}"
    )


@pytest.mark.asyncio
async def test_no_style_intercept_falls_through_to_report_graph(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-command slides message falls through to ``report_stream``."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    _route_slides(monkeypatch)
    await _bootstrap_schema()

    called: dict[str, int] = {"report_stream": 0}

    async def _fake_report_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        called["report_stream"] += 1
        yield FinalOnlyMessage("deck built")

    from paperhub.api import chat as chat_module
    monkeypatch.setattr(chat_module, "report_stream", _fake_report_stream)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None,
                  "user_message": "Generate slides for these papers."},
        ) as response:
            events = await _consume_sse(response.aiter_bytes())

    # The slide pipeline IS the default — the message must reach it.
    assert called["report_stream"] == 1, (
        "report_stream was not invoked for a non-command slides message"
    )

    # The final reply is the LangGraph's final-only message, NOT the
    # intercept's "reset" wording.
    final_evts = [e for e in events if e[0] == "final"]
    assert final_evts, events
    final_text = final_evts[-1][1]["content"]
    assert final_text == "deck built"
    assert "reset" not in final_text.lower()
