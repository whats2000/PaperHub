"""Tool framework for the core agent: specs, context, outcomes, registry."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import aiosqlite

from paperhub.config import Settings
from paperhub.llm.adapter import ToolCall
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.models.domain import AgentState
from paperhub.tracing.tracer import Tracer


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
        async with ctx.tracer.step(
            agent="agent", tool=f"tool:{call.name}", model=None
        ) as step:
            step.record_args({**call.arguments, "call_id": call.id})
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
                return ToolOutcome(
                    content=PENDING_CONTENT.format(summary=described),
                    record={"pending_action_id": verdict.pending_id,
                            "tool": spec.name, "args": call.arguments,
                            "args_summary": described, "target": target},
                    events=[("confirm_request", {
                        "action_id": verdict.pending_id, "tool": spec.name,
                        "args_summary": summary, "target": target,
                        "reason": verdict.reason,
                        "reason_kind": verdict.reason_kind})],
                )
        return await self._invoke(spec, ctx, call.arguments)

    async def _invoke(
        self, spec: ToolSpec, ctx: ToolContext, args: dict[str, Any],
    ) -> ToolOutcome:
        try:
            return await spec.handler(ctx, args)
        except Exception as exc:  # noqa: BLE001 - surface to the model, not crash
            return error_outcome(f"{spec.name} failed: {type(exc).__name__}: {exc}")

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
