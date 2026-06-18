"""In-process run broker for resumable chat streaming (Task A1, FR-15).

A "run" is one chat turn, executed as a backend-owned ``asyncio.Task`` whose SSE
event stream is buffered in a per-run :class:`RunHandle`. The handle holds:

* an append-only ``events`` buffer (the full SSE history for replay),
* a set of live subscriber queues (the originating tab + any reattaching tabs),
* a terminal/eviction lifecycle (``done`` event + ``evict_at`` TTL).

This module is pure data structures — no FastAPI, no agent logic, no clock
reads. Callers that need "now" pass ``time.monotonic()`` in; this keeps the
broker deterministic under test.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

EVICT_TTL_SECONDS = 60.0
"""Seconds a terminal handle is retained for late reattach before eviction."""


@dataclass
class RunHandle:
    """Per-run state backing one resumable chat turn's SSE stream."""

    run_id: int
    task: asyncio.Task[Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(
        default_factory=set
    )
    status: str = "running"  # running|ok|error|cancelled|interrupted
    final_message_id: int | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    evict_at: float | None = None

    def emit(self, event: dict[str, Any]) -> None:
        """Append ``event`` to the buffer and fan it out to every subscriber."""
        self.events.append(event)
        for q in self.subscribers:
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        """Register and return a fresh empty queue.

        The queue is NOT pre-seeded with past events: the caller replays
        ``events`` (via :meth:`events_since`) before draining the queue.
        """
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any] | None]) -> None:
        """Detach a subscriber queue; no-op if already gone."""
        self.subscribers.discard(q)

    def events_since(self, cursor: int) -> tuple[list[dict[str, Any]], int]:
        """Return ``(events[cursor:], len(events))`` for delta polling."""
        return self.events[cursor:], len(self.events)

    def mark_terminal(self, status: str, *, now: float) -> None:
        """Transition to a terminal state. Idempotent.

        Sets ``status`` and ``evict_at``, fires ``done``, and pushes a ``None``
        sentinel to every subscriber (closing their stream). The sentinel is
        NOT appended to ``events``. A second call is a no-op.
        """
        if self.done.is_set():
            return
        self.status = status
        self.evict_at = now + EVICT_TTL_SECONDS
        self.done.set()
        for q in self.subscribers:
            q.put_nowait(None)


class RunBroker:
    """Registry of live :class:`RunHandle` objects keyed by ``run_id``."""

    def __init__(self) -> None:
        self._handles: dict[int, RunHandle] = {}

    def register(self, run_id: int) -> RunHandle:
        """Create, store, and return a new :class:`RunHandle` for ``run_id``."""
        handle = RunHandle(run_id=run_id)
        self._handles[run_id] = handle
        return handle

    def get(self, run_id: int) -> RunHandle | None:
        """Return the handle for ``run_id``, or ``None`` if unknown."""
        return self._handles.get(run_id)

    def evict_expired(self, now: float) -> None:
        """Drop handles whose ``evict_at`` is set and ``<= now``."""
        expired = [
            run_id
            for run_id, handle in self._handles.items()
            if handle.evict_at is not None and handle.evict_at <= now
        ]
        for run_id in expired:
            del self._handles[run_id]
