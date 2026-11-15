"""Tool framework for the core agent: specs, context, outcomes, registry."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import aiosqlite

from paperhub import approvals
from paperhub.agents.core.private_material import collect_chunks
from paperhub.config import Settings
from paperhub.db.connection import write_transaction
from paperhub.db.migrate import INTERRUPTED_ACTION_ERROR
from paperhub.llm.adapter import ToolCall
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.models.domain import AgentState
from paperhub.outbound.context import mark_incomplete
from paperhub.tracing.tracer import Tracer, tool_call_scope

logger = logging.getLogger(__name__)


class Tier(StrEnum):
    """How much user consent a tool needs before it acts."""

    FREE = "free"
    SHOW_UNDO = "show_undo"
    IF_ASKED = "if_asked"
    CONFIRM = "confirm"


@dataclass
class ToolContext:
    conn: aiosqlite.Connection
    tracer: Tracer
    session_id: int
    run_id: int
    settings: Settings
    adapter: LiteLlmAdapter
    emit: Callable[[str, dict[str, Any]], Awaitable[None]]
    enabled_papers: list[tuple[int, str]]  # (paper_content_id, title)
    history: list[dict[str, Any]]
    state: AgentState
    # MCP registry (sql / web / memory servers); None when unavailable.
    mcp_registry: Any = None
    # UI events of every call that finished this turn, in completion order:
    # a stopped turn still records the side effects that already happened.
    done_events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class ToolOutcome:
    """``content`` is what the model sees; ``record`` is what the trace stores.
    ``final_answer``, when set, is a user-ready answer: a step whose only call
    returns one ends the turn with it instead of another model step."""

    content: str
    record: dict[str, Any]
    chunk_ids_read: list[int] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    final_answer: str | None = None
    # Figure images for the model (``view_figure``), sent after the step's
    # tool messages; never stored in the trace.
    images: list[Any] = field(default_factory=list)


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutcome]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    tier: Tier
    handler: ToolHandler
    # Calls of serial tools in one step run one after another, in call
    # order (they read-modify-write shared state, e.g. the chat's deck).
    serial: bool = False


PENDING_CONTENT = "Waiting for the user to confirm {summary}; do not retry."
# H5a: the user dismissed the confirmation card of a paused run (variant
# D-C of a 3-variant comparison, writing-agent-prompts): it says the action
# was not done, so the answer reports that instead of implying it happened.
DECLINED_CONTENT = (
    "The user dismissed {summary}, so it was not done. Do not call it again; "
    "tell the user it was not done.")


#: The agent's paper search: in Ask first mode it waits for the user's OK
#: before anything leaves (SRS §III-10.5, H5a).
ASK_FIRST_SEARCH_TOOL = "search_papers"


async def _search_needs_ok(ctx: ToolContext, args: dict[str, Any]) -> bool:
    """Whether this search call asks first: a live chat turn in Ask first
    mode whose chat has not allowed searches. Unattended / background
    callers (no live turn), the approved held search's follow-up, and calls
    the tool would refuse anyway never ask."""
    if not approvals.is_open(ctx.run_id) or ctx.state.get("approved_held_id"):
        return False
    if ctx.mcp_registry is None or args.get("mode", "search") not in ("search", "suggest"):
        return False
    async with ctx.conn.execute(
        "SELECT auto_approve, allow_searches FROM chat_sessions WHERE id = ?",
        (ctx.session_id,),
    ) as cur:
        row = await cur.fetchone()
    return row is not None and not row[0] and not row[1]


def error_outcome(message: str) -> ToolOutcome:
    """A readable failure for the model; ``record['error']`` marks the trace row."""
    return ToolOutcome(content=f"Error: {message}", record={"error": message})


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.parameters,
                },
            }
            for s in self._tools.values()
        ]

    async def run(self, ctx: ToolContext, call: ToolCall) -> ToolOutcome:
        """Run one call inside a traced step. Never raises: unknown tools,
        malformed arguments and handler failures come back as an error
        outcome the model can read."""
        # H5a wave 3: this row and every row recorded inside the call carry
        # its id (``tool_call_id``), so interleaved parallel calls group.
        with tool_call_scope(call.id):
            return await self._run_traced(ctx, call)

    async def _run_traced(self, ctx: ToolContext, call: ToolCall) -> ToolOutcome:
        async with ctx.tracer.step(
            agent="agent", tool=f"tool:{call.name}", model=None
        ) as step:
            step.record_args({**call.arguments, "call_id": call.id})
            # H5a: the run knows this call is in flight (a wait inside it,
            # or in a task it starts, pauses the run only when every running
            # call waits on a card).
            with approvals.call_scope(ctx.run_id, call.id, call.name):
                outcome = await self._execute(ctx, call)
            ctx.done_events.extend(outcome.events)
            failed = bool(outcome.record.get("error"))
            step.record_result(
                {**outcome.record, "status": "error" if failed else "ok"}
            )
            if failed:
                step.mark_error(str(outcome.record["error"]))
            return outcome

    async def _execute(self, ctx: ToolContext, call: ToolCall) -> ToolOutcome:
        spec = self._tools.get(call.name)
        if spec is None:
            return error_outcome(
                f"Unknown tool {call.name!r}. Available tools: "
                f"{', '.join(self._tools)}."
            )
        if "_raw" in call.arguments:
            return error_outcome(
                f"The arguments for {call.name} were not valid JSON; "
                "call it again with a well-formed JSON object."
            )
        missing = [
            k for k in spec.parameters.get("required", [])
            if k not in call.arguments
        ]
        if missing:
            return error_outcome(
                f"Missing required argument(s) for {call.name}: "
                f"{', '.join(missing)}."
            )
        if spec.name == ASK_FIRST_SEARCH_TOOL and await _search_needs_ok(ctx, call.arguments):
            return await self._await_search(ctx, spec, call.arguments)
        if spec.tier is Tier.CONFIRM:
            from paperhub.agents.core.permissions import (
                gate,
                resolve_target,
                target_summary,
            )

            target = await resolve_target(ctx, spec.name, call.arguments)
            if isinstance(target, str):  # e.g. a guessed id: no card
                return error_outcome(target)
            summary = target_summary(target)
            verdict = await gate(ctx, spec, call.arguments, summary)
            if not verdict.run_now:
                described = f"{spec.name} {summary}"
                event = ("confirm_request", {
                    "action_id": verdict.pending_id, "tool": spec.name,
                    "args_summary": summary, "target": target,
                    "reason": verdict.reason,
                    "reason_kind": verdict.reason_kind})
                if verdict.pending_id is not None and approvals.is_open(ctx.run_id):
                    return await self._await_confirm(
                        ctx, spec, call.arguments, verdict.pending_id,
                        described, target, event)
                return ToolOutcome(
                    content=PENDING_CONTENT.format(summary=described),
                    record={"pending_action_id": verdict.pending_id,
                            "tool": spec.name, "args": call.arguments,
                            "args_summary": described, "target": target},
                    events=[event],
                )
        return await self._invoke(spec, ctx, call.arguments)

    async def _await_confirm(
        self, ctx: ToolContext, spec: ToolSpec, args: dict[str, Any],
        action_id: str, described: str, target: dict[str, Any],
        event: tuple[str, dict[str, Any]],
    ) -> ToolOutcome:
        """H5a: a live turn pauses on the confirmation card. The card is
        shown now (not at the end of the call); Confirm runs the action here,
        in the turn's own context, and writes the ``pending_actions`` row;
        Dismiss tells the model it was declined. Stop cancels the wait."""
        await ctx.emit(*event)
        ctx.done_events.append(event)
        decision = await approvals.wait(
            run_id=ctx.run_id, kind="action", row_id=action_id, tracer=ctx.tracer)
        key = f"action:{action_id}"
        confirm = {"action_id": action_id, "args_summary": described,
                   "target": target}
        if not decision.approved:
            approvals.settle(key, {"status": "dismissed"})
            return ToolOutcome(
                content=DECLINED_CONTENT.format(summary=described),
                record={"confirm": {**confirm, "decision": "dismissed"}})
        try:
            outcome = await self._invoke(spec, ctx, args)
        except BaseException:
            await asyncio.shield(_finish_action(
                ctx.conn, action_id, "failed", {"error": INTERRUPTED_ACTION_ERROR}))
            approvals.settle(key, None)
            raise
        error = outcome.record.get("error")
        result: dict[str, Any] = {"content": outcome.content, "record": outcome.record}
        await asyncio.shield(_finish_action(
            ctx.conn, action_id, "failed" if error else "done",
            {"error": str(error)} if error else result))
        approvals.settle(key, {"status": "failed", "error": str(error)} if error
                         else {"status": "done", "result": result})
        outcome.record = {**outcome.record,
                          "confirm": {**confirm, "decision": "confirmed"}}
        return outcome

    async def _await_search(
        self, ctx: ToolContext, spec: ToolSpec, args: dict[str, Any],
    ) -> ToolOutcome:
        """H5a: in Ask first mode the agent's search call pauses on the
        "Allow this search?" card before anything leaves. Allow (possibly
        with an edited query, or for every later search in this chat) runs
        the search here, in the turn; Don't allow tells the model, with the
        user's optional note. The search's own follow-up queries still pass
        the outbound checkpoint one by one. Stop cancels the wait."""
        from paperhub.agents.core.action_tools import render_action_content
        from paperhub.agents.core.permissions import create_pending
        from paperhub.outbound.destinations import search_destinations

        query = str(args.get("query", ""))
        mode = str(args.get("mode", "search"))
        action_id = await create_pending(ctx, spec.name, args)
        event = ("confirm_request", {
            "action_id": action_id, "tool": spec.name, "args_summary": query,
            "target": {"kind": "search", "query": query, "mode": mode,
                       "destinations": search_destinations()},
            "reason": "Ask first is on for this chat", "reason_kind": "ask_first"})
        await ctx.emit(*event)
        ctx.done_events.append(event)
        decision = await approvals.wait(
            run_id=ctx.run_id, kind="search", row_id=action_id, tracer=ctx.tracer)
        note = (decision.note or "").strip() or None
        ask: dict[str, Any] = {
            "action_id": action_id,
            "decision": "allowed" if decision.approved else "declined",
            "edited": decision.edited, "allow_chat": decision.allow_chat,
            "by_chat_setting": decision.by_chat_setting, "note": note}
        if not decision.approved:
            record = {"query": query, "mode": mode, "declined": True,
                      "note": note, "ask": ask}
            return ToolOutcome(
                content=render_action_content(spec.name, record), record=record)
        sent = (decision.text or "").strip() or query
        try:
            outcome = await self._invoke(spec, ctx, {**args, "query": sent})
        except BaseException:
            await asyncio.shield(_finish_action(
                ctx.conn, action_id, "failed", {"error": INTERRUPTED_ACTION_ERROR}))
            raise
        error = outcome.record.get("error")
        outcome.record = {**outcome.record, "ask": ask}
        if not error:
            # The model reads the result rendered from the record, so the
            # feedback export rebuilds it (an edited query is named there).
            outcome.content = render_action_content(spec.name, outcome.record)
        await asyncio.shield(_finish_action(
            ctx.conn, action_id, "failed" if error else "done",
            {"error": str(error)} if error else {
                "decision": "allowed", "text": sent, "edited": decision.edited,
                "allow_chat": decision.allow_chat,
                "by_chat_setting": decision.by_chat_setting}))
        return outcome

    async def _invoke(
        self, spec: ToolSpec, ctx: ToolContext, args: dict[str, Any],
    ) -> ToolOutcome:
        try:
            outcome = await spec.handler(ctx, args)
        except Exception as exc:  # noqa: BLE001 - surface to the model, not crash
            return error_outcome(f"{spec.name} failed: {type(exc).__name__}: {exc}")
        # Outbound review (H5a): chunk text read from a private paper joins
        # the turn's private material. Gather's inner calls pass through here.
        # ``hit_ids`` is produced by search_chunks' record.
        seen = list(outcome.chunk_ids_read) + [
            i for i in outcome.record.get("hit_ids", []) if isinstance(i, int)]
        if seen:
            try:
                await collect_chunks(ctx.conn, seen)
            except Exception as exc:  # noqa: BLE001 - never break a read over provenance
                logger.warning("outbound: private-material collection failed",
                               exc_info=True)
                mark_incomplete(f"chunk collection failed ({type(exc).__name__})")
                outcome.record["private_collect_error"] = type(exc).__name__
        return outcome

    async def execute_confirmed(
        self, ctx: ToolContext, action_id: str, name: str, args: dict[str, Any],
    ) -> ToolOutcome:
        """Run a user-confirmed tool call directly (no gate, no LLM)."""
        spec = self._tools.get(name)
        if spec is None:
            return error_outcome(f"Unknown tool {name!r}.")
        async with ctx.tracer.step(
            agent="agent", tool=f"tool:{name}", model=None
        ) as step:
            step.record_args({**args, "confirmed_action_id": action_id})
            outcome = await self._invoke(spec, ctx, args)
            failed = bool(outcome.record.get("error"))
            step.record_result(
                {**outcome.record, "status": "error" if failed else "ok",
                 "confirmed_action_id": action_id}
            )
            if failed:
                step.mark_error(str(outcome.record["error"]))
            return outcome


async def _finish_action(
    conn: aiosqlite.Connection, action_id: str, status: str, result: dict[str, Any],
) -> None:
    """Write a confirmed action's final status + result (the endpoint left
    the row claimed: still ``pending`` with a claim marker)."""
    async with write_transaction(conn):
        await conn.execute(
            "UPDATE pending_actions SET status = ?, result_json = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, json.dumps(result), action_id))
