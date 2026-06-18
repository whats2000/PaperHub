"""Resumable-streaming tests for the A2 refactor (FR-15).

The chat turn now runs as a backend-owned background ``asyncio.Task`` whose SSE
events are buffered in a :class:`RunHandle`; ``POST /chat`` returns a thin
*subscriber* stream that replays the buffer then drains a per-subscriber queue.

The load-bearing invariants under test:

1. **Same stream** — a mocked chitchat turn yields the SAME client-visible event
   sequence as before (session → routing_decision → token(s) → final).
2. **Disconnect ≠ cancel** — dropping the subscriber early leaves the background
   ``run_agent`` task running to a terminal status AND persists the assistant
   message row. Only the explicit Stop endpoint (A3) cancels.
3. **Replay** — a SECOND subscriber attached after the run finished still
   receives the full event sequence from the start (from ``handle.events``).
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

from paperhub.app import create_app
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema


class _FakeMcpRegistry:
    """Minimal stand-in for MCPRegistry (the agent is mocked, never dispatched)."""

    async def aggregate_tool_schemas(self) -> list[Any]:
        return []

    async def has_tool(self, name: str) -> bool:
        return False

    async def call(self, name: str, args: dict[str, Any]) -> Any:  # pragma: no cover
        raise RuntimeError("registry should not be called")


def _wire_test_app() -> FastAPI:
    app = create_app()
    app.state.mcp_registry = _FakeMcpRegistry()
    return app


async def _bootstrap_schema(tmp_path: Any) -> None:
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


@pytest.fixture(autouse=True)
def _clear_broker() -> Iterator[None]:
    """Reset the module-level broker + live-task set between tests.

    The broker is a process singleton; each test uses a fresh DB whose run_ids
    restart at 1, so without a reset a stale handle from a prior test would
    shadow the new run.
    """
    import paperhub.api.chat as chat_module

    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()
    yield
    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()


def _chitchat_env(monkeypatch: Any, tmp_path: Any, reply: str = "Hello there!") -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small",'
        '"confidence":0.9,"reasoning":"greeting"}',
    )
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", reply)


# ---------------------------------------------------------------------------
# (1) Same stream — subscriber replays the identical event sequence.
# ---------------------------------------------------------------------------
async def test_chat_subscriber_streams_same_sequence(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    _chitchat_env(monkeypatch, tmp_path)
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
    # Unchanged client-visible sequence: session first, then routing, tokens, final.
    assert types[0] == "session"
    assert "routing_decision" in types
    assert types.count("tool_step") >= 2  # router + chitchat
    assert "final" in types
    final_payload = next(d for t, d in events if t == "final")
    assert final_payload["content"] == "Hello there!"


# ---------------------------------------------------------------------------
# (2) Disconnect ≠ cancel — the task runs to terminal + persists the message.
# ---------------------------------------------------------------------------
async def test_disconnect_does_not_cancel_run(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small",'
        '"confidence":0.9,"reasoning":"greeting"}',
    )
    await _bootstrap_schema(tmp_path)

    # A chitchat stream that yields several tokens with small awaits so we can
    # disconnect mid-stream while the background task is still running.
    async def _slow_chitchat(state: Any, **kwargs: Any) -> AsyncIterator[str]:
        for i in range(5):
            await asyncio.sleep(0.02)
            yield f"tok{i} "

    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "chitchat_stream", _slow_chitchat)

    app = _wire_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hi"},
        ) as response:
            assert response.status_code == 200
            # Consume the first event or two, then break out (= disconnect).
            seen = 0
            async for _chunk in response.aiter_bytes():
                seen += 1
                if seen >= 1:
                    break

    # The run handle exists; its background task must finish on its own.
    handle = next(iter(chat_module.broker._handles.values()))
    assert handle.task is not None
    await asyncio.wait_for(handle.task, timeout=5.0)
    assert handle.status == "ok", (
        f"disconnect must not cancel: expected ok, got {handle.status!r}"
    )

    # And the assistant message row was persisted by the background task.
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT content FROM messages WHERE run_id = ? AND role = 'assistant'",
        (handle.run_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None, "background task must persist the assistant message"
    assert row[0] == "tok0 tok1 tok2 tok3 tok4 "

    # The runs row is terminal 'ok'.
    async with aiosqlite.connect(settings.db_path) as conn, conn.execute(
        "SELECT status FROM runs WHERE id = ?", (handle.run_id,),
    ) as cur:
        run_row = await cur.fetchone()
    assert run_row is not None
    assert run_row[0] == "ok"


# ---------------------------------------------------------------------------
# (3) Replay — a second subscriber attached after the run finished gets it all.
# ---------------------------------------------------------------------------
async def test_second_subscriber_replays_full_sequence(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    _chitchat_env(monkeypatch, tmp_path)
    await _bootstrap_schema(tmp_path)
    app = _wire_test_app()
    transport = ASGITransport(app=app)

    import paperhub.api.chat as chat_module

    async with AsyncClient(transport=transport, base_url="http://test") as client:  # noqa: SIM117
        async with client.stream(
            "POST", "/chat",
            json={"session_id": None, "user_message": "hi"},
        ) as response:
            assert response.status_code == 200
            first_events = await _consume_sse(response.aiter_bytes())

    handle = next(iter(chat_module.broker._handles.values()))
    await asyncio.wait_for(handle.task, timeout=5.0)  # type: ignore[arg-type]
    assert handle.status == "ok"

    # A second subscriber attached AFTER terminal must still see the full
    # sequence by replaying handle.events, then the terminal sentinel closes it.
    q = handle.subscribe()
    replay = list(handle.events)
    replay_types = [e["event"] for e in replay]

    first_types = [t for t, _ in first_events]
    assert replay_types == first_types, (
        f"replay must match the originating stream: {replay_types} != {first_types}"
    )
    assert replay_types[0] == "session"
    assert "final" in replay_types

    # The terminal sentinel is delivered to a fresh subscriber on next emit/None.
    handle.mark_terminal(handle.status, now=0.0)  # idempotent; already terminal
    handle.unsubscribe(q)
