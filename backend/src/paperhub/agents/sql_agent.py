"""SQL Agent — the `library_stats` intent (SRS v2.16, §III-3).

Rewritten (Plan "SQL Agent ReAct rework", Task 3) from the fixed
plan->query->repair-once->answer pipeline into a BOUNDED ReAct LOOP. Each round
the orchestrator LLM returns a :class:`SqlRoundAction`:

* ``action="query"`` — run ONE validated read-only SELECT; the returned
  columns/rows are fed back into the next round's prompt so the model can
  observe and refine.
* ``action="finalize"`` — stop with the user-facing prose ``answer`` plus a
  curated ``papers`` shortlist.

The loop mirrors ``sl_outline.run_sl_outline``: ``for round_num in range(1,
max_rounds+1)``, build a context block, ``adapter.structured(...)``, branch on
the action, force-finalize on the last round via ``must_finalize``.

This task delivers the loop + tracing and streams ``final_action.answer`` as
token(s). **Task 4** turns ``final_action.papers`` into curated
``SearchResultsYield`` cards emitted BEFORE the answer tokens — see the SEAM
comment in :func:`sql_agent_stream`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

import aiosqlite

from paperhub.agents._mcp_result import normalize_mcp_result
from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.research import SearchCandidate, SearchResultsYield, ToolStepYield
from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.sql_domain import SqlPaperPick, SqlRoundAction
from paperhub.tracing.tracer import Tracer

# Maximum query rounds before the loop forces a finalize. The Nth (last) round
# is run with ``must_finalize=True`` so the model cannot request another query.
_MAX_ROUNDS = 4
# Row-text caps for the query_results context block so a wide/long result set
# can't blow up the prompt. Mirrors sl_outline's bounded read-block.
_MAX_ROWS_IN_CONTEXT = 30
_MAX_CELL_CHARS = 200


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


def _cols(schema: Any) -> str:
    """Render a ``sql.describe`` result as a comma-joined column-name list.

    ``sql.describe`` returns ``{"columns": [{name, type}, ...]}`` (the dict
    envelope survives the MCP wire as one valid-JSON TextContent; a top-level
    list is flattened into an unparseable multi-object string — the run-517
    bug). Tolerate a bare list for legacy in-test callers.
    """
    cols = schema.get("columns") if isinstance(schema, dict) else schema
    if isinstance(cols, list):
        return ", ".join(c["name"] for c in cols if isinstance(c, dict) and "name" in c)
    return "(unavailable)"


def _format_query_result(sql: str, result: Any) -> str:
    """Render one prior round's (sql, result) into a readable context block.

    Caps rows (``_MAX_ROWS_IN_CONTEXT``) and truncates long cells
    (``_MAX_CELL_CHARS``) so the accumulated context can't explode the prompt.
    Errors / rejections / empty results are surfaced verbatim so the model can
    refine on the next round.
    """
    header = f"SQL: {sql}"
    if not isinstance(result, dict):
        return f"{header}\nResult: (execution failed — non-dict result)"
    if "error" in result:
        reason = result.get("reason") or result.get("error") or "error"
        return f"{header}\nResult: ERROR ({result.get('error')}): {reason}"
    columns = result.get("columns", [])
    rows = result.get("rows", []) or []
    if not rows:
        return f"{header}\nColumns: {columns}\nRows: (empty result)"

    def _cell(v: Any) -> str:
        s = str(v)
        return s if len(s) <= _MAX_CELL_CHARS else s[:_MAX_CELL_CHARS] + "…"

    shown = rows[:_MAX_ROWS_IN_CONTEXT]
    body = "\n".join("  " + " | ".join(_cell(c) for c in row) for row in shown)
    more = f"\n  … ({len(rows) - len(shown)} more rows)" if len(rows) > len(shown) else ""
    return f"{header}\nColumns: {columns}\nRows ({len(rows)}):\n{body}{more}"


def _coerce_finalize(action: SqlRoundAction) -> SqlRoundAction:
    """Turn a stray ``action="query"`` on the final round into a finalize.

    Never run a query past the cap: if the model still asked to query when it
    was told to finalize, salvage whatever answer/papers it gave (or synthesize
    a minimal answer) rather than executing a (max_rounds+1)-th query.
    """
    if action.action == "finalize":
        return action
    answer = action.answer or (
        "I reached the query limit while looking into your library. Based on "
        "what I gathered above, I wasn't able to finish refining the query."
    )
    return SqlRoundAction(
        action="finalize", sql=None, answer=answer, papers=action.papers,
    )


async def _build_curated_candidates(
    picks: list[SqlPaperPick],
    *,
    conn: aiosqlite.Connection,
    session_id: int | None,
) -> list[SearchCandidate]:
    """Resolve the finalize ``picks`` into ``library:<id>`` ``SearchCandidate``s.

    Two queries total (NOT per-pick): ONE ``SELECT id, title, year FROM
    paper_content WHERE id IN (...)`` to resolve title/year (and to detect a
    hallucinated id — one not present is SKIPPED), and ONE membership query
    ``SELECT paper_content_id FROM papers WHERE session_id = ?`` to set
    ``already_in_session``. The LLM's pick ORDER is preserved; a duplicate
    paper_content_id emits at most one card (first wins).
    """
    # Dedup pick ids, first-occurrence order preserved.
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for pick in picks:
        pcid = pick.paper_content_id
        if pcid in seen:
            continue
        seen.add(pcid)
        ordered_ids.append(pcid)
    if not ordered_ids:
        return []

    # ONE query: resolve title/year for every distinct picked id. Ids absent
    # from the result are hallucinated and get skipped below.
    placeholders = ", ".join("?" for _ in ordered_ids)
    resolved: dict[int, tuple[str, int | None]] = {}
    async with conn.execute(
        f"SELECT id, title, year FROM paper_content WHERE id IN ({placeholders})",
        tuple(ordered_ids),
    ) as cur:
        for row in await cur.fetchall():
            year = int(row[2]) if row[2] is not None else None
            resolved[int(row[0])] = (str(row[1]), year)

    # ONE membership query for already_in_session.
    in_session: set[int] = set()
    if session_id is not None:
        async with conn.execute(
            "SELECT paper_content_id FROM papers WHERE session_id = ?",
            (session_id,),
        ) as cur:
            in_session = {int(r[0]) for r in await cur.fetchall()}

    # Map picks → candidates in the LLM's order; skip hallucinated ids.
    reason_by_id = {p.paper_content_id: p.reason for p in picks}
    candidates: list[SearchCandidate] = []
    for pcid in ordered_ids:
        if pcid not in resolved:
            continue  # hallucinated id — no card
        title, year = resolved[pcid]
        candidates.append(
            SearchCandidate(
                paper_id=f"library:{pcid}",
                title=title,
                year=year,
                already_in_session=pcid in in_session,
                reason=reason_by_id.get(pcid, ""),
                finalize=False,
            )
        )
    return candidates


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
    """Run the bounded ReAct loop for a ``library_stats`` turn.

    Yields, in order: ``ToolStepYield`` (when ``emit_tool_steps``) as each agent
    step commits, then ``str`` answer tokens. Task 4 will additionally yield a
    ``SearchResultsYield`` from ``final_action.papers`` BEFORE the answer tokens
    (the seam is marked below).

    The ``planner_mock`` / ``repair_mock`` / ``answer_mock`` kwargs are retained
    for signature compatibility with chat.py + the old test suite; the ReAct
    loop drives the model via ``adapter.structured`` and does not use them
    (real-API behaviour is unchanged; the mocks were a stream-adapter feature).
    """
    question = effective_query(state)
    language = response_language(state)
    session_id = state.get("session_id")
    # The ReAct loop uses one model for every round; keep the planner model as
    # the loop model (answer_model is retained for signature compatibility).
    model = planner_model

    # Progressive trace-streaming (FR-02): when ``emit_tool_steps`` is set the
    # caller (chat.py) wants each agent step to surface as a ``tool_step`` SSE
    # event AS IT COMMITS — not in one batch at end-of-turn. The Tracer only
    # writes to the DB (no live channel), so we drain newly-committed rows after
    # each step and yield them. Start after the current max step so the router's
    # already-emitted step 0 isn't re-sent.
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

    # 1. Describe schemas once. These two columns lists are constant per turn.
    pc_schema = await _mcp_call(tracer, registry, "sql.describe", {"table": "paper_content"})
    p_schema = await _mcp_call(tracer, registry, "sql.describe", {"table": "papers"})
    async for ev in _drain_new_steps():
        yield ev
    table_schemas = f"paper_content columns: {_cols(pc_schema)}\npapers columns: {_cols(p_schema)}"

    # 2. Recalled-memory block (FR-10): the unconditional active-memory block so
    # a standing directive like "respond in Japanese" always surfaces. The
    # agent prompt has no dedicated memory slot, so we prepend it to the
    # question the model reasons over (cheapest non-lossy injection; if a
    # dedicated prompt var is wanted later, add it to sql_agent/v1).
    memory_context: str = ""
    if conn is not None and recall_enabled:
        memory_context = await build_active_memory_block(conn, session_id=session_id)
    question_for_model = question
    if memory_context:
        question_for_model = f"{question}\n\n{memory_context}"

    # 3. The bounded ReAct loop.
    query_results: list[str] = []  # readable blocks of prior rounds' (sql, rows)
    final_action: SqlRoundAction | None = None

    for round_num in range(1, _MAX_ROUNDS + 1):
        must_finalize = round_num == _MAX_ROUNDS
        results_block = "\n\n".join(query_results) if query_results else "(no queries run yet)"

        async with tracer.step(agent="sql", tool="sql:react", model=model) as step:
            step.record_args({
                "round_number": round_num,
                "max_rounds": _MAX_ROUNDS,
                "must_finalize": must_finalize,
                "question": question,
                "recall_hit": bool(memory_context),
            })
            action: SqlRoundAction = await adapter.structured(
                slot="sql_agent/v1",
                variables={
                    "session_id": session_id,
                    "response_language": language,
                    "round_number": round_num,
                    "max_rounds": _MAX_ROUNDS,
                    "must_finalize": must_finalize,
                    "table_schemas": table_schemas,
                    "question": question_for_model,
                    "query_results": results_block,
                },
                response_model=SqlRoundAction,
                model=model,
            )
            if must_finalize:
                action = _coerce_finalize(action)
            step.record_result(
                {"action": action.action, "sql": action.sql}
                if action.action == "query"
                else {"action": action.action, "n_papers": len(action.papers)}
            )
        async for ev in _drain_new_steps():
            yield ev

        if action.action == "finalize":
            final_action = action
            break

        # action == "query" (and not the must_finalize round, which coerced
        # above). Run the validated query and feed the rows back next round.
        result = await _mcp_call(tracer, registry, "sql.query", {"sql": action.sql or ""})
        async for ev in _drain_new_steps():
            yield ev
        # A rejected / errored / empty result is appended verbatim so the agent
        # can observe and refine — never crash the loop.
        query_results.append(_format_query_result(action.sql or "", result))

    # The loop always terminates with a finalize (break) or the must_finalize
    # coercion on round _MAX_ROUNDS. Defensive fallback for an empty queue.
    if final_action is None:  # pragma: no cover - loop guarantees a finalize
        final_action = SqlRoundAction(
            action="finalize", sql=None,
            answer="I wasn't able to complete the analysis.", papers=[],
        )

    # Emit the curated library cards BEFORE the answer tokens so they render
    # alongside the prose. ``final_action.papers`` (list[SqlPaperPick]) is the
    # model's curated shortlist; each is resolved to a ``library:<pcid>``
    # SearchCandidate (title/year/already_in_session via ``conn``/``session_id``,
    # the reason carried through). The aggregate path (empty picks) emits no
    # card; a card needs the DB so we skip gracefully when ``conn is None``.
    final_picks: list[SqlPaperPick] = final_action.papers
    if final_picks and conn is not None:
        candidates = await _build_curated_candidates(
            final_picks, conn=conn, session_id=session_id
        )
        if candidates:
            yield SearchResultsYield(candidates=candidates)

    # 4. Stream the finalized answer. Wrapped in a tracer step so the trace
    # records the final output text + which round produced it.
    answer = final_action.answer or ""
    async with tracer.step(agent="sql", tool="sql:answer", model=model) as step:
        step.record_args({"n_papers": len(final_action.papers)})
        # Minimal streaming this task: yield the answer in one chunk. Task 4 may
        # stream finer-grained if it streams from the finalize result.
        if answer:
            yield answer
        step.record_result({
            "length": len(answer),
            "n_papers": len(final_action.papers),
            "recall_hit": bool(memory_context),
        })
    async for ev in _drain_new_steps():
        yield ev
