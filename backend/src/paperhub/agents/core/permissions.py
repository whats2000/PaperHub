"""Consent gate for the core agent's tools (spec III-10.4).

FREE / SHOW_UNDO / IF_ASKED tools run at once (IF_ASKED is a prompt rule, not
a code guard). A CONFIRM tool asks the user first; with the chat's
auto-approve on, a small classifier decides whether the user already asked for
exactly this action, and any doubt or failure falls back to asking.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape

from pydantic import BaseModel

from paperhub.config import small_tier_model
from paperhub.db.connection import write_transaction

if TYPE_CHECKING:
    from paperhub.agents.core.tools import ToolContext, ToolSpec

APPROVE_SLOT = "agent_autoapprove/v2"


class ApproveDecision(BaseModel):
    run: bool
    reason: str


@dataclass
class GateResult:
    run_now: bool
    pending_id: str | None
    reason: str
    # Why the user is asked: "required" (auto-approve off), "classifier"
    # (it was not sure) or "classifier_failed"; the card localizes it.
    reason_kind: str = "required"


def summarize_args(ctx: ToolContext, tool: str, args: dict[str, Any]) -> str:
    """A generic call description (``tool(k=v, ...)``) for a confirm-tier
    tool with no target lookup."""
    del ctx
    parts = [f"{key}={value!r}" for key, value in args.items()]
    return f"{tool}({', '.join(parts)})"


def target_summary(target: dict[str, Any]) -> str:
    """The one-line action target the confirmation card falls back to and
    the auto-approve classifier reads: ``"Title" (paper 4)`` or
    ``memory 12: "content"`` (the eval corpus's format)."""
    kind = target.get("kind")
    if kind == "paper":
        return f'"{target["title"]}" (paper {target["id"]})'
    if kind == "memory":
        return f'memory {target["id"]}: "{target["content"]}"'
    return str(target.get("summary") or "")


async def visible_memories(ctx: ToolContext) -> list[tuple[int, str, str]]:
    """``(id, scope, content)`` of the active memories this chat sees (the
    same set its ``<memories>`` block lists)."""
    from paperhub.agents.memory_recall import active_memories

    return await active_memories(ctx.conn, session_id=ctx.session_id, limit=1000)


def memory_listing(rows: list[tuple[int, str, str]]) -> str:
    return "; ".join(f'{mid}: "{content}"' for mid, _, content in rows) or "none"


async def resolve_target(
    ctx: ToolContext, tool: str, args: dict[str, Any],
) -> dict[str, Any] | str:
    """What a confirm-tier call would act on, looked up so the user (and the
    classifier) see the paper title or the memory text, not a bare id.
    Returns an error message when the target is not one this chat can act
    on (e.g. a guessed id), so no confirmation is ever asked for it."""
    if tool == "remove_paper":
        paper = args.get("paper")
        titles = dict(ctx.enabled_papers)
        if not isinstance(paper, int) or isinstance(paper, bool) or paper not in titles:
            listing = "; ".join(f"{p}: {t}" for p, t in ctx.enabled_papers) or "none"
            return (f"Paper {paper!r} is not enabled in this chat. "
                    f"Enabled papers: {listing}.")
        return {"kind": "paper", "id": paper, "title": titles[paper]}
    if tool == "forget":
        mid = args.get("memory_id")
        rows = await visible_memories(ctx)
        found = next((r for r in rows if r[0] == mid), None)
        if not isinstance(mid, int) or isinstance(mid, bool) or found is None:
            return (f"Memory {mid!r} is not an active memory of this chat. "
                    f"Active memories: {memory_listing(rows)}.")
        return {"kind": "memory", "id": mid, "content": found[2], "scope": found[1]}
    return {"kind": "other", "summary": summarize_args(ctx, tool, args)}


def approve_variables(tool: str, summary: str, user_message: str) -> dict[str, str]:
    """The classifier's template inputs, XML-escaped (they sit inside tags).
    The eval runner builds its inputs with this same function."""
    return {"tool": tool, "args_summary": escape(summary),
            "user_message": escape(user_message)}


async def _auto_approve_on(ctx: ToolContext) -> bool:
    async with ctx.conn.execute(
        "SELECT auto_approve FROM chat_sessions WHERE id = ?", (ctx.session_id,),
    ) as cur:
        row = await cur.fetchone()
    return bool(row[0]) if row else False


def _last_user_message(ctx: ToolContext) -> str:
    msg = ctx.state.get("user_message")
    if isinstance(msg, str) and msg:
        return msg
    for entry in reversed(ctx.history):
        if entry.get("role") == "user" and entry.get("content"):
            return str(entry["content"])
    return ""


def _classifier_model(ctx: ToolContext) -> str:
    return str(
        ctx.settings.agent_autoapprove_model or small_tier_model()
    )


async def _classify(
    ctx: ToolContext, tool: str, summary: str, model: str,
) -> ApproveDecision:
    return await ctx.adapter.structured(
        slot=APPROVE_SLOT,
        variables=approve_variables(tool, summary, _last_user_message(ctx)),
        response_model=ApproveDecision,
        model=model,
    )


async def _create_pending(
    ctx: ToolContext, tool: str, args: dict[str, Any],
) -> str:
    action_id = uuid.uuid4().hex
    async with write_transaction(ctx.conn):
        await ctx.conn.execute(
            "INSERT INTO pending_actions (id, run_id, session_id, tool, args_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (action_id, ctx.run_id, ctx.session_id, tool, json.dumps(args)),
        )
    return action_id


async def gate(
    ctx: ToolContext, spec: ToolSpec, args: dict[str, Any], summary: str,
) -> GateResult:
    from paperhub.agents.core.tools import Tier

    if spec.tier is not Tier.CONFIRM:
        return GateResult(True, None, f"{spec.tier.value} tier runs without asking")

    auto = await _auto_approve_on(ctx)
    model = _classifier_model(ctx) if auto else None
    decision = "ask"
    reason = "confirmation required"
    reason_kind = "required"
    pending_id: str | None = None
    async with ctx.tracer.step(agent="agent", tool="agent:approve", model=model) as step:
        step.record_args({"tool": spec.name, "args": args, "auto_approve": auto})
        if model is not None:
            try:
                verdict = await _classify(ctx, spec.name, summary, model)
                if verdict.run:
                    decision = "run"
                reason = verdict.reason
                reason_kind = "classifier"
            except Exception as exc:  # noqa: BLE001 - fail closed: ask the user
                reason = f"classifier failed ({type(exc).__name__}); asking the user"
                reason_kind = "classifier_failed"
        if decision != "run":
            pending_id = await _create_pending(ctx, spec.name, args)
        step.record_result({
            "tool": spec.name, "args": args, "args_summary": summary,
            "auto_approve": auto, "decision": decision, "reason": reason,
            "pending_id": pending_id,
        })
    return GateResult(decision == "run", pending_id, reason, reason_kind)
