"""F4.5 wiring tests for the chat endpoint's slide-style intercept.

Two behaviours are pinned here (H4: no router; the intercept is gated on
the chat having a deck):

1. A deterministic style-command message (``reset slide style``) in a chat
   with a deck MUST short-circuit BEFORE the agent and the Report subgraph
   (``report_stream``), emit a plain-text confirmation reply, and finalise.
2. A non-style-command slides message (``generate slides for these papers``)
   MUST reach ``report_stream`` through the agent's ``create_deck`` tool.

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
from tests.agent_script import answer, calls, script_agent


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


async def _seed_session_with_deck() -> None:
    """Session 1 with a deck row, so the style intercept applies."""
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO decks (session_id, tex_path) VALUES (1, 'deck.tex')")
        await conn.commit()


@pytest.mark.asyncio
async def test_style_intercept_skips_report_graph(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reset slide style`` short-circuits BEFORE ``report_stream`` runs."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema()
    await _seed_session_with_deck()
    seen = script_agent(monkeypatch, [])  # any model call would fail

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
            json={"session_id": 1, "user_message": "reset slide style"},
        ) as response:
            events = await _consume_sse(response.aiter_bytes())

    # The slide pipeline must NOT have been called — the intercept fired.
    assert called["report_stream"] == 0, (
        "report_stream was invoked despite a deterministic style-command match"
    )
    assert seen == [], "the intercept must not call the model"

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
    await _bootstrap_schema()
    script_agent(monkeypatch, [
        calls(("create_deck", {"brief": "these papers"})),
        answer("deck built"),
    ])

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

    # The final reply is the agent's answer, NOT the intercept's "reset"
    # wording.
    final_evts = [e for e in events if e[0] == "final"]
    assert final_evts, events
    final_text = final_evts[-1][1]["content"]
    assert final_text == "deck built"
    assert "reset" not in final_text.lower()
