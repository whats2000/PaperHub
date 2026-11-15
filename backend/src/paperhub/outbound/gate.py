"""Per-turn outbound gate for a pipeline that sends free text (Plan H5a).

Binds the checkpoint's dependencies (connection, review adapter, tracer, run
and session) once per pipeline run and turns every decision into the chat
event the UI shows (``outbound_rewritten`` / ``outbound_blocked`` /
``outbound_held``). Events are collected in order on ``events`` so the caller
can forward them and persist them on the assistant message.

A chat-origin request the checkpoint asks about pauses a live turn (H5a):
the call waits at its held card until the user decides (no time limit;
Stop ends it), then sends the approved text through the checkpoint with
the approval token, or skips it when the user chose not to send it.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from paperhub import approvals
from paperhub.llm.adapter import LlmAdapter
from paperhub.outbound import held
from paperhub.outbound.checkpoint import check
from paperhub.outbound.models import Decision, OutboundKind, OutboundRequest
from paperhub.tracing.tracer import Tracer

__all__ = ["OUTBOUND_EVENTS", "OutboundGate", "decision_event", "decision_summary"]

OUTBOUND_EVENTS = frozenset({"outbound_rewritten", "outbound_blocked", "outbound_held"})


def decision_event(d: Decision, destination: str) -> dict[str, Any] | None:
    """The chat event for ``d`` (``{"event", "data"}``), or None for a plain
    send or a held request the user discarded (its card shows that)."""
    if d.action == "discard":
        return None
    if d.action == "send":
        if d.sent_text == d.original_text:
            return None
        return {"event": "outbound_rewritten", "data": {
            "log_id": d.log_id, "sent_text": d.sent_text,
            "original_text": d.original_text, "reason": d.reason,
            "destination": destination}}
    if d.action == "block":
        data: dict[str, Any] = {"log_id": d.log_id, "text": d.original_text,
                                "reason": d.reason}
        if d.stage == "terms" and d.term:
            data["term"] = d.term
        return {"event": "outbound_blocked", "data": data}
    return {"event": "outbound_held", "data": {
        "held_id": d.held_id, "held_ids": [d.held_id], "text": d.original_text,
        "reason": d.reason, "destination": destination, "status": "pending"}}


EventSink = Callable[[dict[str, Any]], None]


@dataclass
class OutboundGate:
    conn: aiosqlite.Connection
    adapter: LlmAdapter
    tracer: Tracer | None
    run_id: int | None = None
    session_id: int | None = None
    unattended: bool = False
    #: The follow-up turn's approval token (a held card the user sent): the
    #: checkpoint sends that exact text once without review.
    approved_held_id: str | None = None
    #: Called with each chat event as it is decided (live forwarding), so a
    #: run that fails later still shows the decisions already made.
    on_event: EventSink | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    _held: dict[tuple[str, str, str], Decision] = field(default_factory=dict)
    _locks: dict[tuple[str, str, str], asyncio.Lock] = field(default_factory=dict)
    # One held card per text: the first held row's id keys the card and
    # ``held_ids`` lists every held row of that text (other destinations).
    _cards: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The user's decision per held text in this run: a later held row of a
    # decided card (another destination) takes the same decision.
    _decided: dict[str, approvals.ApprovalDecision] = field(default_factory=dict)

    async def clear(
        self, *, caller: str, kind: OutboundKind, destination: str, text: str,
    ) -> Decision:
        """Run ``text`` through the checkpoint. Send only ``sent_text`` and
        only when ``action == "send"``; anything else skips this one query.

        The same text held once in this run is not held again: the earlier
        decision is returned with no new held row, log row or event."""
        key = (kind, destination, text)
        async with self._locks.setdefault(key, asyncio.Lock()):
            if key in self._held:
                return self._held[key]
            d = await check(
                OutboundRequest(caller=caller, kind=kind, destination=destination,
                                text=text, unattended=self.unattended,
                                run_id=self.run_id, session_id=self.session_id),
                conn=self.conn, adapter=self.adapter, tracer=self.tracer,
                approved_held_id=self.approved_held_id,
            )
            self._show(decision_event(d, destination))
            if d.action == "ask" and d.held_id is not None and self._can_wait():
                d = await self._paused(d, caller=caller, kind=kind,
                                       destination=destination)
                self._held[key] = d
            elif d.action in ("ask", "hold"):
                self._held[key] = d
        return d

    def _can_wait(self) -> bool:
        """A chat-origin request of a live turn waits for the decision;
        unattended callers never do (they use the held-queries list)."""
        return (self.session_id is not None and not self.unattended
                and approvals.is_open(self.run_id))

    def _show(self, event: dict[str, Any] | None) -> None:
        if event is not None and event["event"] == "outbound_held":
            event = self._card(event["data"])
        if event is not None:
            self.events.append(event)
            if self.on_event is not None:
                self.on_event(event)

    async def _paused(
        self, d: Decision, *, caller: str, kind: OutboundKind, destination: str,
    ) -> Decision:
        """Wait at the held card for the user's decision, then act on it in
        this run: send the approved text through the checkpoint (it spends
        the approval) or skip it."""
        assert d.held_id is not None and self.run_id is not None
        text = d.original_text
        card = self._cards[text]
        decision = self._decided.get(text)
        if decision is None:
            decision = await approvals.wait(
                run_id=self.run_id, kind="held", row_id=d.held_id,
                card_id=str(card["held_id"]), tracer=self.tracer)
            self._decided[text] = decision
        else:
            # The card was decided before this row was held: same decision.
            row = await held.get(self.conn, d.held_id)
            if row is not None:
                await held.decide(
                    self.conn, row, "sent" if decision.approved else "discarded",
                    actor="user", sent_text=decision.text if decision.approved else None)
        update = {**card, "status": "sent" if decision.approved else "discarded"}
        if decision.approved:
            update.update(sent_text=decision.text, ran=True)
        card.update(update)
        self._emit_card(dict(card))
        if not decision.approved:
            return Decision("discard", None, text, "user",
                            "the user chose not to send it", None, d.held_id)
        sent = await check(
            OutboundRequest(caller=caller, kind=kind, destination=destination,
                            text=decision.text or text, unattended=False,
                            run_id=self.run_id, session_id=self.session_id),
            conn=self.conn, adapter=self.adapter, tracer=self.tracer,
            approved_held_id=d.held_id,
        )
        if sent.action != "send":
            # E.g. a sensitive term added since: blocked, never held again.
            self._show(decision_event(sent, destination))
        return sent

    def _emit_card(self, data: dict[str, Any]) -> None:
        event = {"event": "outbound_held", "data": data}
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _card(self, data: dict[str, Any]) -> dict[str, Any]:
        """The held card for ``data``'s text. A second held row for the same
        text re-emits the existing card (same ``held_id``) with its id added
        to ``held_ids``: an update of that card, not a new one."""
        card = self._cards.get(data["text"])
        if card is None:
            card = dict(data)
            self._cards[data["text"]] = card
        else:
            card["held_ids"] = [*card["held_ids"], data["held_id"]]
        return {"event": "outbound_held", "data": dict(card)}


def decision_summary(d: Decision) -> dict[str, Any]:
    """What a call site records in its own trace step about the decision."""
    return {"decision": d.action, "stage": d.stage, "sent_text": d.sent_text,
            "log_id": d.log_id, "held_id": d.held_id}
