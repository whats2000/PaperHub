"""SQL Agent — the `library_stats` intent (SRS v2.16, §III-3)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


class _Registry(Protocol):
    async def call(self, namespaced_name: str, args: dict[str, Any]) -> Any: ...


async def _mcp_call(tracer: Tracer, registry: _Registry, tool: str, args: dict[str, Any]) -> Any:
    async with tracer.step(agent="sql", tool=tool, model=None) as step:
        step.record_args(args)
        result = await registry.call(tool, args)
        if isinstance(result, dict) and result.get("error") == "rejected":
            step.mark_rejected(str(result.get("reason", "rejected")))
            step.record_result(result)
        else:
            # Record the real payload so the trace can reconstruct what the
            # agent saw (agent-flow observability policy). Truncate large rows.
            summary: dict[str, Any] = {"ok": True}
            if isinstance(result, dict):
                summary["payload"] = result
            elif isinstance(result, list):
                summary["payload"] = result[:50]
            step.record_result(summary)
        return result


async def _plan_sql(
    adapter: LlmAdapter,
    tracer: Tracer,
    *,
    slot: str,
    model: str,
    variables: dict[str, Any],
    mock: str | None,
) -> str:
    kwargs: dict[str, Any] = {}
    if mock is not None:
        kwargs["mock_response"] = mock
    parts: list[str] = []
    async with tracer.step(agent="sql", tool="sql:plan", model=model) as step:
        step.record_args(variables)
        async for tok in adapter.stream(slot=slot, variables=variables, model=model, **kwargs):
            parts.append(tok)
        sql = "".join(parts).strip().strip("`").removeprefix("sql").strip()
        step.record_result({"sql": sql})
    return sql


async def sql_agent_stream(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    registry: _Registry,
    planner_model: str,
    answer_model: str,
    planner_mock: str | None = None,
    repair_mock: str | None = None,
    answer_mock: str | None = None,
) -> AsyncIterator[str]:
    question = effective_query(state)
    language = response_language(state)
    session_id = state.get("session_id")

    await _mcp_call(tracer, registry, "sql.list_tables", {})

    sql = await _plan_sql(
        adapter,
        tracer,
        slot="sql_planner/v1",
        model=planner_model,
        variables={"session_id": session_id, "question": question},
        mock=planner_mock,
    )

    result = await _mcp_call(tracer, registry, "sql.query", {"sql": sql})
    rows = result.get("rows") if isinstance(result, dict) else None
    if (not isinstance(result, dict)) or ("error" in result) or (not rows):
        schema = await _mcp_call(tracer, registry, "sql.describe", {"table": "papers"})
        repaired = await _plan_sql(
            adapter,
            tracer,
            slot="sql_repair/v1",
            model=planner_model,
            variables={
                "question": question,
                "schema": json.dumps(schema),
                "previous_sql": sql,
                "error": (
                    (result.get("reason") or result.get("error") or "empty result")
                    if isinstance(result, dict)
                    else "execution failed"
                ),
            },
            mock=repair_mock if repair_mock is not None else planner_mock,
        )
        sql = repaired
        result = await _mcp_call(tracer, registry, "sql.query", {"sql": sql})
        rows = result.get("rows") if isinstance(result, dict) else []

    columns = result.get("columns", []) if isinstance(result, dict) else []
    rows = rows or []

    kwargs: dict[str, Any] = {}
    if answer_mock is not None:
        kwargs["mock_response"] = answer_mock
    async with tracer.step(agent="sql", tool="sql:answer", model=answer_model) as step:
        step.record_args({"sql": sql, "row_count": len(rows)})
        collected: list[str] = []
        async for tok in adapter.stream(
            slot="sql_answer/v1",
            variables={
                "question": question,
                "sql": sql,
                "response_language": language,
                "columns": json.dumps(columns),
                "rows": json.dumps(rows),
            },
            model=answer_model,
            **kwargs,
        ):
            collected.append(tok)
            yield tok
        step.record_result({"length": sum(len(c) for c in collected)})
