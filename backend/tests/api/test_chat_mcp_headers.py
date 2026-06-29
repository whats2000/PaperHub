"""The chat endpoint sets the `ClientHeadersContext` around the agent
invocation so any downstream `MCPClient.call_tool` reads the live
`session_id` / `run_id` (Task v2.5-7).

This is the narrowly-focused unit-flavoured test: it monkeypatches
`paper_search` to inspect the contextvar at the exact moment the agent
runs, asserts the session_id and run_id match the run the chat endpoint
allocated, and asserts the contextvar is *reset* after the stream
completes (so it doesn't leak into the next request on the same asyncio
task).

A broader proof that this contextvar reaches `MCPClient` and through to
the FastMCP middleware lives in `test_chat_mcp_loopback.py`.
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
from paperhub.mcp.client_context import (
    ClientHeadersContext,
    current_client_headers_context,
)


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


@pytest.mark.asyncio
async def test_chat_endpoint_sets_client_headers_context_for_paper_search(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paper_search-routed turn must run with the client_headers context
    set to ``(session_id, run_id)`` matching the chat endpoint's allocated
    ids. Captured at the moment the agent generator yields its first item.
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_search",'
        '"confidence":0.95,"reasoning":"search intent"}',
    )
    await _bootstrap_schema()

    captured: dict[str, ClientHeadersContext | None] = {}

    async def _fake_paper_search(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        # The chat endpoint sets the contextvar before invoking us — at
        # this point it MUST be populated with the live session/run ids.
        captured["at_call"] = current_client_headers_context()
        yield FinalOnlyMessage("done")

    from paperhub.api import chat as chat_module
    monkeypatch.setattr(chat_module, "paper_search", _fake_paper_search)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "find me papers"},
        ) as response:
            events = await _consume_sse(response.aiter_bytes())

    # Pull the session/run ids the chat endpoint allocated out of the
    # `session` event.
    sess_evts = [e for e in events if e[0] == "session"]
    assert sess_evts, events
    session_id = int(sess_evts[0][1]["session_id"])
    run_id = int(sess_evts[0][1]["run_id"])

    # Contextvar must have been set with exactly those ids.
    ctx = captured["at_call"]
    assert ctx is not None, "client_headers contextvar was unset during paper_search"
    assert ctx.session_id == session_id
    assert ctx.run_id == run_id

    # And after the stream completes, the contextvar must NOT have leaked
    # into the caller's task. (We're outside the chat handler's task
    # tree, but the finally-reset is still important: in production the
    # asyncio event loop reuses tasks between requests.)
    assert current_client_headers_context() is None


@pytest.mark.asyncio
async def test_chat_endpoint_resets_context_on_error(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the agent raises, the chat endpoint must reset the
    contextvar — otherwise an unhandled exception leaks the session id
    into the next request that happens to run on the same task.
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"paper_search",'
        '"confidence":0.95,"reasoning":"search intent"}',
    )
    await _bootstrap_schema()

    seen_in_agent: list[ClientHeadersContext | None] = []

    async def _exploding_paper_search(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        seen_in_agent.append(current_client_headers_context())
        raise RuntimeError("simulated agent failure")
        yield  # pragma: no cover — async-generator marker

    from paperhub.api import chat as chat_module
    monkeypatch.setattr(chat_module, "paper_search", _exploding_paper_search)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "boom"},
        ) as response:
            await _consume_sse(response.aiter_bytes())

    # The agent saw a populated context...
    assert seen_in_agent and seen_in_agent[0] is not None
    # ...and the finally in the chat endpoint reset it before the
    # generator returned.
    assert current_client_headers_context() is None
