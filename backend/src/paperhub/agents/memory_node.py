"""Memory node — the `memory` intent (SRS v2.16, §III-3).

Extracts op/scope/content/target/confirmation from the user's message, then
writes via the `memory` MCP (registry).  For edit/forget it first recalls the
target.  Returns the model's in-language confirmation (template fallback on
failure/rejection).  A rejected MCP result marks the tracer step
status='rejected'.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from paperhub.agents._mcp_result import normalize_mcp_result
from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


class _Registry(Protocol):
    async def call(self, namespaced_name: str, args: dict[str, Any]) -> Any: ...


def _normalize(result: Any) -> Any:
    """Normalise a raw MCP result.

    Delegates to the shared :func:`~paperhub.agents._mcp_result.normalize_mcp_result`
    which handles JSON-string parsing AND the FastMCP ``{"result": X}`` list-return
    envelope (Bug 2 fix).
    """
    return normalize_mcp_result(result)


async def _mcp(
    tracer: Tracer,
    registry: _Registry,
    tool: str,
    args: dict[str, Any],
) -> Any:
    async with tracer.step(agent="memory", tool=tool, model=None) as step:
        step.record_args(args)
        res = _normalize(await registry.call(tool, args))
        if isinstance(res, dict) and res.get("error") == "rejected":
            step.mark_rejected(str(res.get("reason", "rejected")))
            step.record_result(res)
        else:
            step.record_result(
                res if isinstance(res, dict) else {"count": len(res) if isinstance(res, list) else 0}
            )
        return res


async def memory_node(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    registry: _Registry,
    model: str,
    op_mock: str | None = None,
) -> AgentState:
    """Execute the memory intent: plan the op, then write via the registry."""
    message = effective_query(state)
    language = response_language(state)

    # ── Step 1: extract the operation from the user's message ──────────────
    kwargs: dict[str, Any] = {}
    if op_mock is not None:
        kwargs["mock_response"] = op_mock
    parts: list[str] = []
    async with tracer.step(agent="memory", tool="memory:plan", model=model) as step:
        step.record_args({"user_message": message})
        async for tok in adapter.stream(
            slot="memory_op/v1",
            variables={"user_message": message, "response_language": language},
            model=model,
            **kwargs,
        ):
            parts.append(tok)
        raw_text = "".join(parts).strip()
        # Strip markdown code fences the LLM may wrap around the JSON
        # (e.g. ```json\n{...}\n```).  Mirror the same approach used by
        # sql_agent._plan_sql which strips backticks + the "sql" prefix.
        if raw_text.startswith("```"):
            raw_text = raw_text.lstrip("`")
            # Remove optional language tag (e.g. "json") on the first line.
            if "\n" in raw_text:
                first, rest = raw_text.split("\n", 1)
                # If the first "line" is just a language identifier, drop it.
                raw_text = rest if first.strip().lower() in ("json", "") else first + "\n" + rest
            raw_text = raw_text.rstrip("`").strip()
        op_dict = json.loads(raw_text)
        step.record_result(op_dict)

    kind: str = op_dict.get("op", "")
    scope: str = op_dict.get("scope", "session")
    content: str = op_dict.get("content", "")
    target: str = op_dict.get("target", "")
    confirmation: str = op_dict.get("confirmation") or "Done."

    # ── Step 2: dispatch MCP write ─────────────────────────────────────────
    msg: str
    if kind == "add":
        res = await _mcp(tracer, registry, "memory.add", {"content": content, "scope": scope})
        if isinstance(res, dict) and "error" not in res:
            msg = confirmation
        else:
            reason = res.get("reason", "error") if isinstance(res, dict) else "error"
            msg = f"Couldn't save that: {reason}"

    elif kind in ("edit", "forget"):
        hits = await _mcp(
            tracer, registry, "memory.recall",
            {"query": target or content, "scope": "both"},
        )
        if not isinstance(hits, list) or not hits:
            msg = "I couldn't find a matching note to update."
        else:
            mid: int = hits[0]["id"]
            if kind == "edit":
                res = await _mcp(tracer, registry, "memory.edit", {"memory_id": mid, "content": content})
            else:
                res = await _mcp(tracer, registry, "memory.forget", {"memory_id": mid})
            if isinstance(res, dict) and "error" not in res:
                msg = confirmation
            else:
                reason = res.get("reason", "error") if isinstance(res, dict) else "error"
                msg = f"Couldn't apply that change: {reason}"

    else:
        msg = "I wasn't sure what to remember — could you rephrase?"

    return {**state, "final_response": msg}
