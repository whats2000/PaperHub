"""Tests for the in-process run broker (Task A1, FR-15).

The broker holds per-run :class:`RunHandle` data structures backing resumable
SSE streaming: an append-only event buffer, a set of live subscriber queues, a
terminal/eviction lifecycle. No FastAPI, no agent logic — pure data structures.

Time is injected (``now: float``); the broker never reads the clock itself, so
these tests are deterministic.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from paperhub.api.run_broker import EVICT_TTL_SECONDS, RunBroker, RunHandle

pytestmark = pytest.mark.asyncio


def _drain(q: asyncio.Queue[dict[str, Any] | None]) -> list[dict[str, Any] | None]:
    """Pop everything currently buffered on a queue without blocking."""
    out: list[dict[str, Any] | None] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_emit_appends_and_fans_out_to_subscribers() -> None:
    handle = RunHandle(run_id=1)
    q1 = handle.subscribe()
    q2 = handle.subscribe()

    e1 = {"type": "token", "i": 0}
    e2 = {"type": "token", "i": 1}
    handle.emit(e1)
    handle.emit(e2)

    assert handle.events == [e1, e2]
    assert _drain(q1) == [e1, e2]
    assert _drain(q2) == [e1, e2]


async def test_events_since_returns_tail_and_length() -> None:
    handle = RunHandle(run_id=1)
    a = {"type": "a"}
    b = {"type": "b"}
    c = {"type": "c"}
    for e in (a, b, c):
        handle.emit(e)

    assert handle.events_since(0) == ([a, b, c], 3)
    assert handle.events_since(1) == ([b, c], 3)
    # Cursor at the end yields no new events but the current length.
    assert handle.events_since(3) == ([], 3)


async def test_mark_terminal_sets_state_and_sentinel_idempotent() -> None:
    handle = RunHandle(run_id=1)
    q = handle.subscribe()
    handle.emit({"type": "token"})

    assert not handle.done.is_set()
    handle.mark_terminal("ok", now=100.0)

    assert handle.status == "ok"
    assert handle.done.is_set()
    assert handle.evict_at == 100.0 + EVICT_TTL_SECONDS
    # Subscriber received the prior event then a None sentinel — no None in events.
    assert _drain(q) == [{"type": "token"}, None]
    assert None not in handle.events

    # Idempotent: a second call must not change state or re-fire the sentinel.
    handle.mark_terminal("error", now=999.0)
    assert handle.status == "ok"
    assert handle.evict_at == 100.0 + EVICT_TTL_SECONDS
    assert _drain(q) == []


async def test_midrun_subscriber_replay_then_drain_has_no_gaps() -> None:
    """A subscriber attaching mid-run replays events_since(0) then drains its
    queue, yielding the full ordered sequence with no gaps or duplicates."""
    handle = RunHandle(run_id=1)
    handle.emit({"i": 0})
    handle.emit({"i": 1})

    # Client attaches now (after two events already emitted).
    q = handle.subscribe()
    replayed, cursor = handle.events_since(0)

    # More events arrive after subscription.
    handle.emit({"i": 2})
    handle.emit({"i": 3})
    handle.mark_terminal("ok", now=0.0)

    live = _drain(q)
    # The A2 caller replays the snapshot first, then live queue items beyond it.
    combined = replayed + [e for e in live[: len(live) - 1]]  # drop trailing None
    assert combined == [{"i": 0}, {"i": 1}, {"i": 2}, {"i": 3}]
    assert live[-1] is None  # sentinel closes the stream
    assert cursor == 2


async def test_unsubscribe_removes_queue() -> None:
    handle = RunHandle(run_id=1)
    q = handle.subscribe()
    assert q in handle.subscribers
    handle.unsubscribe(q)
    assert q not in handle.subscribers
    # Emitting after unsubscribe does not feed the detached queue.
    handle.emit({"type": "x"})
    assert _drain(q) == []


async def test_broker_register_get_roundtrip() -> None:
    broker = RunBroker()
    handle = broker.register(7)
    assert handle.run_id == 7
    assert broker.get(7) is handle
    assert broker.get(999) is None


async def test_evict_expired_drops_only_expired_terminal_handles() -> None:
    broker = RunBroker()
    running = broker.register(1)  # no evict_at — still running
    soon = broker.register(2)
    later = broker.register(3)

    soon.mark_terminal("ok", now=0.0)  # evict_at == EVICT_TTL_SECONDS
    later.mark_terminal("ok", now=1000.0)  # evict_at == 1000 + TTL

    # At now == TTL, `soon` is exactly at its evict_at (<= now) → dropped.
    broker.evict_expired(now=EVICT_TTL_SECONDS)

    assert broker.get(1) is running  # running handle untouched
    assert broker.get(2) is None  # expired → dropped
    assert broker.get(3) is later  # not yet expired
