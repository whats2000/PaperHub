"""SQL Agent — the `library_stats` intent (SRS v2.16, §III-3)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import aiosqlite

from paperhub.agents._mcp_result import normalize_mcp_result
from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.research import SearchCandidate, SearchResultsYield, ToolStepYield
from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.db.tool_calls import drain_tool_calls_since
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


async def _emit_library_candidates(
    columns: list[Any],
    rows: list[Any],
    *,
    conn: aiosqlite.Connection | None,
    session_id: int | None,
) -> AsyncIterator[SearchResultsYield]:
    """Map a paper-shaped ``sql.query`` result (one with a ``paper_content_id``
    column) into a single ``SearchResultsYield`` of ``library:<id>`` candidates.

    ``already_in_session`` is resolved with ONE set-membership query against the
    session's ``papers`` rows (not per-row). ``title``/``year`` come from the
    result columns when the SELECT included them, else the dataclass defaults.
    """
    pcid_idx = columns.index("paper_content_id")
    title_idx = columns.index("title") if "title" in columns else None
    year_idx = columns.index("year") if "year" in columns else None

    in_session: set[int] = set()
    if conn is not None and session_id is not None:
        async with conn.execute(
            "SELECT paper_content_id FROM papers WHERE session_id = ?",
            (session_id,),
        ) as cur:
            in_session = {int(r[0]) for r in await cur.fetchall()}

    candidates: list[SearchCandidate] = []
    for row in rows:
        try:
            pcid = int(row[pcid_idx])
        except (TypeError, ValueError, IndexError):
            continue
        title = str(row[title_idx]) if title_idx is not None and row[title_idx] is not None else ""
        year: int | None = None
        if year_idx is not None and row[year_idx] is not None:
            try:
                year = int(row[year_idx])
            except (TypeError, ValueError):
                year = None
        candidates.append(
            SearchCandidate(
                paper_id=f"library:{pcid}",
                title=title,
                year=year,
                already_in_session=pcid in in_session,
                finalize=False,
            )
        )
    yield SearchResultsYield(candidates=candidates)


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
    emit_tool_steps: bool = False,
) -> AsyncIterator[str | ToolStepYield | SearchResultsYield]:
    question = effective_query(state)
    language = response_language(state)
    session_id = state.get("session_id")

    # Progressive trace-streaming (FR-02): when ``emit_tool_steps`` is set the
    # caller (chat.py) wants each agent step to surface as a ``tool_step`` SSE
    # event AS IT COMMITS — not in one batch at end-of-turn via the post-stream
    # drain. The Tracer only writes to the DB (no live channel), so we drain
    # newly-committed rows after each step and yield them. Start after the
    # current max step so the router's already-emitted step 0 isn't re-sent.
    _emitted_step = -1
    if emit_tool_steps:
        async with tracer.connection.execute(
            "SELECT COALESCE(MAX(step_index), -1) FROM tool_calls WHERE run_id = ?",
            (tracer.run_id,),
        ) as cur:
            row = await cur.fetchone()
        _emitted_step = int(row[0]) if row is not None else -1

    async def _drain_new_steps() -> AsyncIterator[ToolStepYield]:
        nonlocal _emitted_step
        if not emit_tool_steps:
            return
        for rec in await drain_tool_calls_since(tracer.connection, tracer.run_id, _emitted_step):
            _emitted_step = rec["step_index"]
            yield ToolStepYield(record=rec)

    pc_schema = await _mcp_call(tracer, registry, "sql.describe", {"table": "paper_content"})
    p_schema = await _mcp_call(tracer, registry, "sql.describe", {"table": "papers"})
    async for ev in _drain_new_steps():
        yield ev

    def _cols(schema: Any) -> str:
        # ``sql.describe`` returns ``{"columns": [{name, type}, ...]}`` (the
        # dict envelope survives the MCP wire as one valid-JSON TextContent;
        # a top-level list is flattened into an unparseable multi-object
        # string — the run-517 bug). Tolerate a bare list for legacy callers.
        cols = schema.get("columns") if isinstance(schema, dict) else schema
        if isinstance(cols, list):
            return ", ".join(c["name"] for c in cols if isinstance(c, dict) and "name" in c)
        return "(unavailable)"

    table_schemas = f"paper_content columns: {_cols(pc_schema)}\npapers columns: {_cols(p_schema)}"

    sql = await _plan_sql(
        adapter,
        tracer,
        slot="sql_planner/v1",
        model=planner_model,
        variables={"session_id": session_id, "question": question, "table_schemas": table_schemas},
        mock=planner_mock,
    )
    async for ev in _drain_new_steps():
        yield ev

    result = await _mcp_call(tracer, registry, "sql.query", {"sql": sql})
    async for ev in _drain_new_steps():
        yield ev
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
        async for ev in _drain_new_steps():
            yield ev
        sql = repaired
        result = await _mcp_call(tracer, registry, "sql.query", {"sql": sql})
        async for ev in _drain_new_steps():
            yield ev
        rows = result.get("rows") if isinstance(result, dict) else []

    columns = result.get("columns", []) if isinstance(result, dict) else []
    rows = rows or []

    # E1: when the executed SELECT is paper-shaped (it includes a
    # ``paper_content_id`` column), surface each row as a ``library:<id>``
    # SearchCandidate so the result is attachable via the Research Agent's
    # existing ``search_results`` SSE path. Emit BEFORE the answer stream so the
    # cards render alongside (not after) the prose. Aggregate queries (no
    # ``paper_content_id`` column) emit nothing.
    if isinstance(columns, list) and "paper_content_id" in columns:
        async for cand_ev in _emit_library_candidates(
            columns, rows, conn=conn, session_id=session_id,
        ):
            yield cand_ev

    # Build recall-injection block (FR-10). Empty when disabled or no memories.
    # Uses the UNCONDITIONAL active-memory block (not FTS) so a standing
    # directive like "respond in Japanese" always surfaces — an FTS query on
    # the user's stats question would never match a language preference.
    memory_context: str = ""
    if conn is not None and recall_enabled:
        memory_context = await build_active_memory_block(
            conn,
            session_id=session_id,
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
    # Stream the answer step's record now that it's committed (its tokens
    # already streamed above). Any remainder is caught by chat.py's
    # post-stream drain — which is now a no-op for the happy path.
    async for ev in _drain_new_steps():
        yield ev
