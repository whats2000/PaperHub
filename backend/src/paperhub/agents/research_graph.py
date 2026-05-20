"""Research Agent subgraphs (Plan C v2.7 — decomposed paper_search).

paper_search subgraph (linear with per-request inner kick-back loop):

    START → ps_parse → ps_process → ps_finalize → END

  * ps_parse splits user_message into N ParsedRequests via the Parser
    LLM (small model, no tools).
  * ps_process fans out per-request (asyncio.gather) — each branch runs
    a bounded Discover→Resolve loop with kick-back-on-not-found
    (MAX_REFINEMENT_LOOPS = 2). web.search is the Discoverer's only
    tool; papers.search_semantic_scholar is called exactly once per
    request by the Resolver (architecturally, not by prompt rule).
  * ps_finalize emits the search_results event deterministically from
    the resolved set (Python builds SearchCandidates; the LLM never
    writes a json:candidates block) and runs the Synthesizer for the
    user-visible prose.

paper_qa subgraph (agentic-hierarchical, v2.10):

    START → pq_resolve → conditional_edges → {
        "empty":    pq_empty       → END
        "dispatch": pq_dispatch    → pq_finalize → END
    }

  * pq_dispatch fans out ``run_paper_qa_subagent`` per enabled paper via
    asyncio.gather; each subagent runs a bounded list_sections/read_section
    loop and returns a PerPaperPicks with cited chunks.
  * pq_finalize streams the finalizer LLM (flagship model) over the raw
    chunk picks — no intermediate analyst prose (raw chunks > summaries
    for correctness).

Streaming contract (consumed by ``api/chat.py``):

  * ``stream_mode="custom"`` carries ``tool_step`` / ``search_results``
    / ``token`` events written via ``langgraph.config.get_stream_writer()``.
  * ``stream_mode="values"`` carries the final state snapshot so the
    chat layer lifts ``state["final_response"]``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import aiosqlite
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from paperhub.agents.paper_qa_subagent import (
    MAX_SECTION_READS,
    PerPaperPicks,
    run_paper_qa_subagent,
)
from paperhub.agents.research import (
    SearchCandidate,
    _resolve_enabled_papers,
    paper_qa_finalize,
)
from paperhub.agents.research_pipeline import (
    MAX_REFINEMENT_LOOPS,
    CanonicalIdentity,
    ParsedRequest,
    ResolvedPaper,
    discover_canonical,
    parse_user_message,
    resolve_via_ss,
    synthesize_prose,
)
from paperhub.agents.state import AgentState, effective_query
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.mcp.registry import MCPRegistry
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import Tracer

ResearchExtraKwargs = dict[str, Any]
PaperSearchFn = Callable[..., AsyncIterator[Any]]


@dataclass
class ResearchDeps:
    """Per-request dependencies bound into the research subgraph at build
    time via closure. Rebuilt every chat turn (LangGraph compile is cheap).
    """

    adapter: LlmAdapter
    tracer: Tracer
    paper_qa_model: str
    conn: aiosqlite.Connection
    pipeline: PaperPipeline
    retriever: Retriever
    mcp_registry: MCPRegistry
    # Optional adapter kwargs (e.g. ``mock_response`` injected by smoke tests).
    adapter_kwargs: ResearchExtraKwargs | None = None
    # Paper-search Parser/Synthesizer model. Defaults to ``paper_qa_model``
    # but the pipeline can use a cheaper small model for the Parser without
    # affecting the Synthesizer's prose quality.
    paper_search_parser_model: str | None = None
    paper_search_synth_model: str | None = None
    # v2.10: per-paper subagent model + read budget. Optional with None
    # default — same shape as paper_search_parser_model / synth_model
    # above. Consumer (_pq_dispatch) falls back to deps.paper_qa_model
    # for the model and the module-level MAX_SECTION_READS for the
    # budget when None. Avoids a hardcoded model literal here that
    # would silently shadow Settings overrides if a caller forgot to
    # thread the field through (run-89 failure pattern).
    paper_qa_subagent_model: str | None = None
    paper_qa_max_section_reads: int | None = None


def _kwargs(deps: ResearchDeps) -> ResearchExtraKwargs:
    return dict(deps.adapter_kwargs or {})


# ---------------------------------------------------------------------------
# paper_search subgraph
# ---------------------------------------------------------------------------


def build_paper_search_subgraph(deps: ResearchDeps) -> Any:
    """Compile the v2.7 decomposed paper_search subgraph.

    Topology::

        START → ps_parse → ps_process → ps_finalize → END

    Per-request fan-out (parallel via ``asyncio.gather``) happens inside
    ``ps_process``; each branch runs a Discover→Resolve loop with kick-
    back-on-not-found, capped by ``MAX_REFINEMENT_LOOPS``.

    Streaming: every node drains ``drain_tool_calls_since`` between
    stages and pushes each new row via ``get_stream_writer()`` as a
    ``tool_step`` custom event, so the frontend trace panel sees each
    stage close in real time (NOT a single batch at the end).
    """

    parser_model = deps.paper_search_parser_model or deps.paper_qa_model
    synth_model = deps.paper_search_synth_model or deps.paper_qa_model

    async def _drain_and_stream_tool_steps(
        last_step: int, run_id: int,
    ) -> int:
        """Drain tracer rows newer than ``last_step`` and emit them as
        ``tool_step`` custom events. Returns the new last step_index.
        Called between every pipeline stage so SSE clients see progress
        as each stage closes."""
        writer = get_stream_writer()
        recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
        for rec in recs:
            writer({"event": "tool_step", "record": rec})
            last_step = rec["step_index"]
        return last_step

    async def _ps_parse(state: AgentState) -> AgentState:
        run_id: int = state["run_id"]
        last_step = int(state.get("ps_last_step_index", -1))
        requests = await parse_user_message(
            effective_query(state),
            tracer=deps.tracer,
            model=parser_model,
            **_kwargs(deps),
        )
        last_step = await _drain_and_stream_tool_steps(last_step, run_id)
        return {
            **state,
            "ps_parsed_requests": list(requests),
            "ps_last_step_index": last_step,
        }

    async def _ps_process(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        run_id: int = state["run_id"]
        last_step = int(state.get("ps_last_step_index", -1))
        requests: list[ParsedRequest] = list(state.get("ps_parsed_requests") or [])
        # Lock-guarded incremental drain: each gather task calls this
        # after every Discoverer iteration / Resolver call so the
        # frontend trace panel sees rows as they close, not all-at-once
        # 40+ seconds later when gather() resolves. Same pattern as
        # _pq_map below.
        drain_lock = asyncio.Lock()

        async def _emit_progress() -> None:
            nonlocal last_step
            async with drain_lock:
                recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
                for rec in recs:
                    writer({"event": "tool_step", "record": rec})
                    last_step = rec["step_index"]

        async def _process_one(req: ParsedRequest) -> ResolvedPaper | ParsedRequest:
            """Discover → Resolve, exactly one pass.

            web.search is keyword-matching to surface a likely canonical
            title; it is not iterative refinement. So we do one Discover
            attempt and one Resolver call, then trust the result. If the
            Discoverer can't pin down a canonical identity, we fall back
            to the raw hint as a low-confidence identity and still hand
            off to the Resolver — Semantic Scholar's own fuzzy match may
            land the paper even when the LLM couldn't.
            """
            for _iter in range(MAX_REFINEMENT_LOOPS):
                identity = await discover_canonical(
                    req,
                    tracer=deps.tracer,
                    model=parser_model,
                    mcp_registry=deps.mcp_registry,
                    **_kwargs(deps),
                )
                await _emit_progress()
                if identity is None:
                    # Don't abandon the request — let SS try the raw hint.
                    identity = CanonicalIdentity(
                        title=req.hint,
                        author_surname=None,
                        year=None,
                        confidence="low",
                        rationale=(
                            "Discoverer couldn't extract a canonical "
                            "title; passing raw hint to Semantic Scholar."
                        ),
                    )
                resolved = await resolve_via_ss(
                    req, identity,
                    tracer=deps.tracer,
                    mcp_registry=deps.mcp_registry,
                )
                await _emit_progress()
                if resolved is not None:
                    return resolved
            return req  # NotFound

        if requests:
            results = await asyncio.gather(
                *[_process_one(r) for r in requests],
                return_exceptions=False,
            )
        else:
            results = []

        resolved: list[ResolvedPaper] = [
            r for r in results if isinstance(r, ResolvedPaper)
        ]
        not_found: list[ParsedRequest] = [
            r for r in results if isinstance(r, ParsedRequest)
        ]
        last_step = await _drain_and_stream_tool_steps(last_step, run_id)
        return {
            **state,
            "ps_resolved": resolved,
            "ps_not_found": not_found,
            "ps_last_step_index": last_step,
        }

    async def _ps_finalize(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        run_id: int = state["run_id"]
        last_step = int(state.get("ps_last_step_index", -1))
        resolved: list[ResolvedPaper] = list(state.get("ps_resolved") or [])
        not_found: list[ParsedRequest] = list(state.get("ps_not_found") or [])

        # Emit search_results event DETERMINISTICALLY from the resolved
        # set. Block emission is architectural, not LLM-driven — the
        # Synthesizer cannot accidentally drop it.
        candidates: list[SearchCandidate] = []
        if resolved:
            for r in resolved:
                meta = r.meta if isinstance(r.meta, dict) else {}
                year_val = meta.get("year")
                if not isinstance(year_val, int):
                    year_val = r.identity.year
                abstract_val = meta.get("abstract")
                arxiv_val = meta.get("arxiv_id")
                authors_val = meta.get("authors")
                authors_list: list[str] = (
                    [str(a) for a in authors_val]
                    if isinstance(authors_val, list)
                    else []
                )
                candidates.append(
                    SearchCandidate(
                        paper_id=r.paper_id,
                        title=str(meta.get("title") or r.identity.title or ""),
                        authors=authors_list,
                        year=year_val,
                        abstract=str(abstract_val) if isinstance(abstract_val, str) else None,
                        arxiv_id=str(arxiv_val) if isinstance(arxiv_val, str) else None,
                        has_open_pdf=bool(meta.get("has_open_pdf")),
                        reason=(
                            r.identity.rationale
                            or "Discovered via web.search + Semantic Scholar."
                        ),
                        # v2.7 ResolvedPapers only exist when the user
                        # named the paper explicitly AND the Resolver
                        # landed evidence — the Parser already filtered
                        # vague/topic-survey queries to []. So every
                        # candidate here is "user asked, we found":
                        # auto-add to session knowledge base. Cap-and-
                        # already-in-session checks happen in
                        # api.chat._process_search_results.
                        finalize=True,
                    ),
                )
            writer({"event": "search_results", "candidates": candidates})

        # Observability (harness eval): record the candidates emitted to the
        # user + the resolved/not_found breakdown in a dedicated tracer row.
        # The SSE search_results event is ephemeral; this row makes the
        # final paper_search output reconstruct-able from tool_calls alone.
        async with deps.tracer.step(
            agent="research", tool="paper_search:finalize", model=None,
        ) as fin_step:
            fin_step.record_args({
                "resolved_count": len(resolved),
                "not_found_count": len(not_found),
            })
            fin_step.record_result({
                "emitted_candidates": [
                    {"paper_id": c.paper_id, "title": c.title, "finalize": c.finalize}
                    for c in candidates
                ],
                "resolved_count": len(resolved),
                "not_found": [req.hint for req in not_found],
            })

        prose = await synthesize_prose(
            resolved,
            not_found,
            user_message=effective_query(state),
            tracer=deps.tracer,
            model=synth_model,
            **_kwargs(deps),
        )
        last_step = await _drain_and_stream_tool_steps(last_step, run_id)
        return {
            **state,
            "final_response": prose,
            "ps_last_step_index": last_step,
        }

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("ps_parse", _ps_parse)
    g.add_node("ps_process", _ps_process)
    g.add_node("ps_finalize", _ps_finalize)
    g.add_edge(START, "ps_parse")
    g.add_edge("ps_parse", "ps_process")
    g.add_edge("ps_process", "ps_finalize")
    g.add_edge("ps_finalize", END)
    return g.compile()


# ---------------------------------------------------------------------------
# paper_qa subgraph (v2.10 — agentic hierarchical)
# ---------------------------------------------------------------------------


def build_paper_qa_subgraph(deps: ResearchDeps) -> Any:
    """Compile the agentic-hierarchical paper_qa subgraph (Plan C v2.10).

    Topology::

        START → pq_resolve
        pq_resolve → conditional_edges → {
            "empty":    pq_empty       → END
            "dispatch": pq_dispatch    → pq_finalize → END
        }

    ``pq_dispatch`` fans out ``run_paper_qa_subagent`` per enabled paper via
    asyncio.gather. ``pq_finalize`` reads each subagent's PickedChunks
    DIRECTLY (not analyst-prose-summary) and streams the user-facing
    answer. Raw chunks > summaries for correctness (flagship LLM + raw
    chunks preserves [chunk:N] markers verbatim).
    """

    async def _pq_resolve(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        last_step = int(state.get("ps_last_step_index", -1))
        papers = await _resolve_enabled_papers(
            deps.conn, session_id=state["session_id"], tracer=deps.tracer,
        )
        # Drain and emit the paper_qa:resolve tool_step that just closed.
        run_id: int = state["run_id"]
        recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
        for rec in recs:
            writer({"event": "tool_step", "record": rec})
            last_step = rec["step_index"]
        return {**state, "pq_papers": papers, "ps_last_step_index": last_step}

    def _pq_branch(state: AgentState) -> str:
        n = len(state.get("pq_papers") or [])
        return "empty" if n == 0 else "dispatch"

    async def _pq_empty(state: AgentState) -> AgentState:
        return {
            **state,
            "final_response": (
                "No references are enabled for this session. Add a paper "
                "to the Reference Sources panel first, then ask again."
            ),
        }

    async def _pq_dispatch(state: AgentState) -> AgentState:
        """Fan-out: one subagent task per enabled paper, asyncio.gather.

        Drain bookkeeping uses a ``set[int]`` of emitted step_indexes rather
        than a monotonic ``last_step`` watermark. A monotonic watermark
        permanently loses a row when two subagents commit their tracer
        steps out-of-order — the tracer assigns step_index at OPEN time but
        commits at CLOSE time, so a subagent that opened first but
        finished slower commits a LOWER step_index AFTER its sibling, and
        a watermark-based drain advances past the lower index before it
        was ever read. The set-based dedup is robust against any
        commit-order interleaving.
        """
        writer = get_stream_writer()
        papers = list(state["pq_papers"])
        run_id: int = state["run_id"]
        baseline_step = int(state.get("ps_last_step_index", -1))
        emitted_indices: set[int] = set()
        lock = asyncio.Lock()

        # Fall back to the flagship paper_qa_model when no subagent-specific
        # model was wired in, matching the paper_search_parser_model /
        # paper_search_synth_model pattern at the top of this file.
        subagent_model = deps.paper_qa_subagent_model or deps.paper_qa_model
        max_reads = (
            deps.paper_qa_max_section_reads
            if deps.paper_qa_max_section_reads is not None
            else MAX_SECTION_READS
        )

        async def _one_with_emit(pid: int, title: str) -> PerPaperPicks:
            picks = await run_paper_qa_subagent(
                paper_content_id=pid,
                title=title,
                user_message=state["user_message"],
                tracer=deps.tracer,
                model=subagent_model,
                conn=deps.conn,
                max_section_reads=max_reads,
                **_kwargs(deps),
            )
            async with lock:
                recs = await drain_tool_calls_since(
                    deps.conn, run_id, baseline_step,
                )
                for rec in recs:
                    if rec["step_index"] not in emitted_indices:
                        writer({"event": "tool_step", "record": rec})
                        emitted_indices.add(rec["step_index"])
            return picks

        picks_list: list[PerPaperPicks] = list(
            await asyncio.gather(*[_one_with_emit(pid, title) for pid, title in papers])
        )
        next_last_step = max(emitted_indices) if emitted_indices else baseline_step
        return {
            **state,
            "pq_per_paper_picks": picks_list,
            "ps_last_step_index": next_last_step,
        }

    async def _pq_finalize(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        picks: list[PerPaperPicks] = list(state.get("pq_per_paper_picks") or [])
        if not picks or all(not p.picked_chunks for p in picks):
            return {
                **state,
                "final_response": (
                    "I checked every enabled reference but none contained "
                    "content relevant to your question. Try a more specific "
                    "question or add more references."
                ),
            }
        collected: list[str] = []
        async for tok in paper_qa_finalize(
            per_paper_picks=picks,
            user_message=state["user_message"],
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.paper_qa_model,
            state=state,
            **_kwargs(deps),
        ):
            writer({"event": "token", "text": tok})
            collected.append(tok)
        return {**state, "final_response": "".join(collected)}

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("pq_resolve", _pq_resolve)
    g.add_node("pq_empty", _pq_empty)
    g.add_node("pq_dispatch", _pq_dispatch)
    g.add_node("pq_finalize", _pq_finalize)
    g.add_edge(START, "pq_resolve")
    g.add_conditional_edges("pq_resolve", _pq_branch, {
        "empty": "pq_empty",
        "dispatch": "pq_dispatch",
    })
    g.add_edge("pq_empty", END)
    g.add_edge("pq_dispatch", "pq_finalize")
    g.add_edge("pq_finalize", END)
    return g.compile()


# ---------------------------------------------------------------------------
# Outer Research dispatcher subgraph
# ---------------------------------------------------------------------------


def _dispatch_branch(state: AgentState) -> str:
    return state["routing_decision"].intent


async def _dispatch(state: AgentState) -> AgentState:
    return state


def build_research_subgraph(deps: ResearchDeps) -> Any:
    """Compile the Research Agent dispatcher subgraph.

    Routes on ``state["routing_decision"].intent`` to either the
    paper_search or paper_qa subgraph (compiled subgraphs are embedded
    as nodes — supported by LangGraph 1.2.0)::

        START → research_dispatch → conditional_edges → {
            "paper_search": paper_search_subgraph
            "paper_qa":     paper_qa_subgraph
        }
        each → END
    """
    ps_subgraph = build_paper_search_subgraph(deps)
    pq_subgraph = build_paper_qa_subgraph(deps)

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("research_dispatch", _dispatch)
    g.add_node("paper_search", ps_subgraph)
    g.add_node("paper_qa", pq_subgraph)
    g.add_edge(START, "research_dispatch")
    g.add_conditional_edges(
        "research_dispatch",
        _dispatch_branch,
        {"paper_search": "paper_search", "paper_qa": "paper_qa"},
    )
    g.add_edge("paper_search", END)
    g.add_edge("paper_qa", END)
    return g.compile()
