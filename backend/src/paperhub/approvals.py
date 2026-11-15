"""Approval waiters: a live chat turn pauses on its cards (Plan H5a).

Anything in a chat turn that needs the user's approval — a held outbound
search or an Ask-first (confirm-tier) action — pauses the run where it is
until the user decides or presses Stop (SRS §III-10.5 "Approval pauses the
run"). There is no time limit: Stop cancels the run task, which ends the
wait.

A live turn opens a :class:`RunScope` (``open_run``); a tool call inside it
waits with :func:`wait`, keyed ``held:<id>``, ``action:<id>`` or
``search:<id>`` (an Ask-first search call, a ``pending_actions`` row). The card's
endpoint hands the decision over with :func:`deliver`; the run itself then
does the work (sends the search, runs the action), so its events and trace
steps belong to the turn. With no live waiter (backend restarted mid-pause)
the endpoints keep their earlier path.

The scope tracks which tool calls are in flight (``call_scope``) so it can
tell the stream ``paused`` only when every running call is waiting on a
card, and ``resumed`` when a decision unblocks it. Waits are in-process
state: they do not survive a restart, and nothing here touches the DB.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from paperhub.tracing.tracer import Tracer

__all__ = [
    "ApprovalDecision",
    "RunScope",
    "call_scope",
    "close_run",
    "deliver",
    "has_waiter",
    "is_open",
    "open_run",
    "route_events",
    "settle",
    "wait",
]

Kind = Literal["held", "action", "search"]
Emit = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class ApprovalDecision:
    """The user's decision on one card. ``text`` is the search text the
    user sent (held / search; possibly edited, then ``edited``)."""

    approved: bool
    text: str | None = None
    edited: bool = False
    #: Search cards only: "Don't allow"'s optional instruction for the agent,
    #: and "Allow searches in this chat".
    note: str | None = None
    allow_chat: bool = False
    #: Search cards only: allowed because the user chose "Allow searches in
    #: this chat" on another card of the same chat, not on this one.
    by_chat_setting: bool = False


@dataclass(frozen=True)
class _Call:
    id: str
    tool: str


@dataclass
class _Waiter:
    kind: Kind
    #: The row the decision is delivered to (``held:<row>`` / ``action:<id>``).
    key: str
    #: The card the UI shows (a held card covers several rows).
    card_id: str
    run_id: int
    call: _Call | None
    decision: asyncio.Future[ApprovalDecision]
    #: Set by the run once it acted on an approval (a confirmed action's
    #: ``{status, result|error}``); the endpoint may await it.
    outcome: asyncio.Future[dict[str, Any]]


@dataclass
class RunScope:
    run_id: int
    emit: Emit | None = None
    active: dict[str, _Call] = field(default_factory=dict)
    waiters: dict[str, _Waiter] = field(default_factory=dict)
    paused_cards: tuple[tuple[str, str, str, str], ...] | None = None

    def _cards(self) -> tuple[tuple[str, str, str, str], ...]:
        """(kind, card id, tool, tool call id) of each waiting card; the call
        id lets the activity timeline mark that call's node as waiting."""
        seen: dict[tuple[str, str], tuple[str, str]] = {}
        for w in self.waiters.values():
            seen.setdefault((w.kind, w.card_id),
                            (w.call.tool, w.call.id) if w.call else ("", ""))
        return tuple((k, c, t, i) for (k, c), (t, i) in seen.items())

    def refresh(self) -> None:
        """Emit ``paused`` (the waiting cards) when every running call waits
        on a card, or the card list changed while paused; ``resumed`` when a
        paused run has work running again."""
        waiting_calls = {w.call.id for w in self.waiters.values() if w.call}
        cards = self._cards()
        paused = bool(cards) and set(self.active) <= waiting_calls
        if paused:
            if cards != self.paused_cards:
                self.paused_cards = cards
                self._send("paused", {"cards": [
                    {"kind": k, "id": c, "tool": t, "call_id": i}
                    for k, c, t, i in cards]})
        elif self.paused_cards is not None:
            self.paused_cards = None
            self._send("resumed", {})

    def _send(self, name: str, data: dict[str, Any]) -> None:
        if self.emit is not None:
            self.emit(name, {"run_id": self.run_id, **data})


_scopes: dict[int, RunScope] = {}
_waiters: dict[str, _Waiter] = {}
_current_call: contextvars.ContextVar[_Call | None] = contextvars.ContextVar(
    "approval_call", default=None)


def open_run(run_id: int, emit: Emit | None = None) -> RunScope:
    """Start a live turn's scope: its calls may now wait on cards."""
    scope = RunScope(run_id, emit)
    _scopes[run_id] = scope
    return scope


def close_run(run_id: int) -> None:
    """End a turn's scope. Waits still open (the run is ending) are
    cancelled, so no endpoint keeps a decision for a run that is gone."""
    scope = _scopes.pop(run_id, None)
    if scope is None:
        return
    for key in [k for k, w in _waiters.items() if w.run_id == run_id]:
        w = _waiters.pop(key)
        w.decision.cancel()
        w.outcome.cancel()
    scope.waiters.clear()


def route_events(run_id: int, emit: Emit) -> Emit | None:
    """Send the run's paused / resumed events through ``emit`` instead (the
    agent loop's own event queue, so they stay in order with the cards);
    returns the emitter it replaced."""
    scope = _scopes.get(run_id)
    if scope is None:
        return None
    prev, scope.emit = scope.emit, emit
    return prev


def is_open(run_id: int | None) -> bool:
    return run_id is not None and run_id in _scopes


def has_waiter(key: str) -> bool:
    w = _waiters.get(key)
    return w is not None and not w.decision.done()


@contextlib.contextmanager
def call_scope(run_id: int, call_id: str, tool: str) -> Iterator[None]:
    """Mark one tool call of ``run_id`` as running (tasks it starts inherit
    it), so a wait inside it is attributed to it."""
    scope = _scopes.get(run_id)
    call = _Call(call_id, tool)
    token = _current_call.set(call)
    if scope is not None:
        scope.active[call_id] = call
        scope.refresh()
    try:
        yield
    finally:
        _current_call.reset(token)
        if scope is not None:
            scope.active.pop(call_id, None)
            scope.refresh()


def deliver(key: str, decision: ApprovalDecision) -> asyncio.Future[dict[str, Any]] | None:
    """Hand ``decision`` to the run waiting on ``key``; returns the
    future of the run's outcome, or None when no live run waits on it."""
    w = _waiters.get(key)
    if w is None or w.decision.done():
        return None
    w.decision.set_result(decision)
    return w.outcome


def settle(key: str, outcome: dict[str, Any] | None) -> None:
    """The run reports what it did with an approved action (see
    ``deliver``) and forgets the waiter; None: it could not finish."""
    w = _waiters.pop(key, None)
    if w is None or w.outcome.done():
        return
    if outcome is None:
        w.outcome.cancel()
    else:
        w.outcome.set_result(outcome)


async def wait(
    *, run_id: int, kind: Kind, row_id: str, card_id: str | None = None,
    tracer: Tracer | None,
) -> ApprovalDecision:
    """Pause until the user decides ``kind:row_id`` (no time limit; a
    cancelled run ends the wait). Traced as ``agent:await_approval``."""
    scope = _scopes[run_id]
    loop = asyncio.get_running_loop()
    key = f"{kind}:{row_id}"
    w = _Waiter(kind, key, card_id or row_id, run_id, _current_call.get(),
                loop.create_future(), loop.create_future())
    started = time.monotonic()
    step_cm: Any = (tracer.step(agent="agent", tool="agent:await_approval", model=None)
                    if tracer is not None else contextlib.nullcontext(None))
    async with step_cm as step:
        if step is not None:
            step.record_args({"kind": kind, "id": row_id, "card_id": w.card_id})
        _waiters[key] = w
        scope.waiters[key] = w
        scope.refresh()
        try:
            decision = await w.decision
        finally:
            decided = w.decision.done() and not w.decision.cancelled()
            scope.waiters.pop(key, None)
            if kind != "action" or not decided:
                # An action's waiter stays until the run settles it.
                _waiters.pop(key, None)
            if _scopes.get(run_id) is scope:
                scope.refresh()
            waited_ms = int((time.monotonic() - started) * 1000)
            if step is not None and not decided:
                step.record_result({"kind": kind, "id": row_id,
                                    "decision": "stopped", "waited_ms": waited_ms})
        if step is not None:
            result: dict[str, Any] = {
                "kind": kind, "id": row_id,
                "decision": "approved" if decision.approved else "declined",
                "edited": decision.edited, "waited_ms": waited_ms}
            if kind == "search":
                result.update(note_given=bool(decision.note),
                              allow_chat=decision.allow_chat,
                              by_chat_setting=decision.by_chat_setting)
            step.record_result(result)
    return decision

