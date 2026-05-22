"""SQL Agent — the `library_stats` intent (SRS v2.16, §III-3)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import aiosqlite

from paperhub.agents._mcp_result import normalize_mcp_result
from paperhub.agents.memory_recall import build_memory_context_block
from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


class _Registry(Protocol):
    async def call(self, namespaced_name: str, args: dict[str, Any]) -> Any: ...


def _normalize_mcp_result(raw: Any) -> Any:
    """Normalise the return value of ``MCPClient.call_tool``.

    Thin re-export of :func:`~paperhub.agents._mcp_result.normalize_mcp_result`
    kept for backwards compatibility with existing imports in tests.
    """
    return normalize_mcp_result(raw)


async def _mcp_call(tracer: Tracer, registry: _Registry, tool: str, args: dict[str, Any]) -> Any:
    async with tracer.step(agent="sql", tool=tool, model=None) as step:
        step.record_args(args)
        raw = await registry.call(tool, args)
        result = _normalize_mcp_result(raw)
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
    conn: aiosqlite.Connection | None = None,
    recall_enabled: bool = True,
) -> AsyncIterator[str]:
    question = effective_query(state)
    language = response_language(state)
    session_id = state.get("session_id")

    pc_schema = await _mcp_call(tracer, registry, "sql.describe", {"table": "paper_content"})
    p_schema = await _mcp_call(tracer, registry, "sql.describe", {"table": "papers"})

    def _cols(schema: Any) -> str:
        return ", ".join(c["name"] for c in schema) if isinstance(schema, list) else "(unavailable)"

    table_schemas = f"paper_content columns: {_cols(pc_schema)}\npapers columns: {_cols(p_schema)}"

    sql = await _plan_sql(
        adapter,
        tracer,
        slot="sql_planner/v1",
        model=planner_model,
        variables={"session_id": session_id, "question": question, "table_schemas": table_schemas},
        mock=planner_mock,
    )

    result = await _mcp_call(tracer, registry, "sql.query", {"sql": sql})
    rows = result.get("rows") if isinstance(result, dict) else None
    if (not isinstance(result, dict)) or ("error" in result) or (not rows):
        repaired = await _plan_sql(
            adapter,
            tracer,
            slot="sql_repair/v1",
            model=planner_model,
            variables={
                "question": question,
                "schema": json.dumps(pc_schema),
                "previous_sql": sql,
                "table_schemas": table_schemas,
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

    # Build recall-injection block (FR-10). Empty string when disabled or no hits.
    memory_context: str = ""
    if conn is not None:
        memory_context = await build_memory_context_block(
            conn,
            session_id=session_id,
            query=question,
            enabled=recall_enabled,
        )

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
                "memory_context": memory_context,
            },
            model=answer_model,
            **kwargs,
        ):
            collected.append(tok)
            yield tok
        step.record_result({
            "length": sum(len(c) for c in collected),
            "recall_hit": bool(memory_context),
        })
