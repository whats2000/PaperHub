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

paper_qa subgraph (count-branching):

    START → pq_resolve → conditional_edges → {
        "empty":   pq_empty       → END
        "single":  pq_single      → END
        "map":     pq_map         → pq_synthesize → END
    }

Streaming contract (consumed by ``api/chat.py``):

  * ``stream_mode="custom"`` carries ``tool_step`` / ``search_results``
    / ``token`` events written via ``langgraph.config.get_stream_writer()``.
  * ``stream_mode="values"`` carries the final state snapshot so the
    chat layer lifts ``state["final_response"]``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from paperhub.agents.research import (
    FinalOnlyMessage,
    SearchCandidate,
    _paper_qa_map_one,
    _paper_qa_single_stream,
    _paper_qa_synthesize_stream,
    _resolve_enabled_papers,
)
from paperhub.agents.research import (
    paper_qa_stream as _default_paper_qa_stream,
)
from paperhub.agents.research_pipeline import (
    MAX_REFINEMENT_LOOPS,
    ParsedRequest,
    ResolvedPaper,
    discover_canonical,
    parse_user_message,
    resolve_via_ss,
    synthesize_prose,
)
from paperhub.agents.state import AgentState
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.mcp.registry import MCPRegistry
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import Tracer

ResearchExtraKwargs = dict[str, Any]
PaperSearchFn = Callable[..., AsyncIterator[Any]]
PaperQaStreamFn = Callable[..., AsyncIterator[Any]]


@dataclass
class ResearchDeps:
    """Per-request dependencies bound into the research subgraph at build
    time via closure. Rebuilt every chat turn (LangGraph compile is cheap).

    ``paper_search_fn`` / ``paper_qa_stream_fn`` are retained for
    backwards-compatibility with callers that want to inject a fake
    end-to-end async generator (e.g. ``chat.py`` exposes the legacy
    ``paper_search`` / ``paper_qa_stream`` module-level attributes so
    ``test_chat_sse.py`` can monkeypatch them with fakes — that path
    bypasses the subgraph entirely and feeds a fake generator straight
    into the SSE translation loop). The subgraph nodes themselves call
    the underlying helpers directly via the other fields.
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
    # Legacy generator hooks (paper_qa only — paper_search no longer has
    # a legacy generator). Kept so test_chat_sse.py can monkeypatch
    # paper_qa_stream with a fake.
    paper_qa_stream_fn: PaperQaStreamFn = field(default=_default_paper_qa_stream)


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
            state["user_message"],
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
        run_id: int = state["run_id"]
        last_step = int(state.get("ps_last_step_index", -1))
        requests: list[ParsedRequest] = list(state.get("ps_parsed_requests") or [])

        async def _process_one(req: ParsedRequest) -> ResolvedPaper | ParsedRequest:
            """Run a bounded Discover→Resolve loop for ONE request.
            Returns ResolvedPaper on success, the original ParsedRequest
            on exhaustion (signal to the Synthesizer that this one failed).
            """
            feedback = ""
            for _iter in range(MAX_REFINEMENT_LOOPS):
                identity = await discover_canonical(
                    req,
                    tracer=deps.tracer,
                    model=parser_model,
                    mcp_registry=deps.mcp_registry,
                    prior_attempt_feedback=feedback,
                    **_kwargs(deps),
                )
                if identity is None:
                    feedback = (
                        f"Discoverer couldn't pin down a canonical "
                        f"identity for '{req.hint}'. Try entirely "
                        f"different query angles next attempt."
                    )
                    continue
                resolved = await resolve_via_ss(
                    req, identity,
                    tracer=deps.tracer,
                    mcp_registry=deps.mcp_registry,
                )
                if resolved is not None:
                    return resolved
                feedback = (
                    f"Resolver tried Semantic Scholar with a query built "
                    f"from title='{identity.title}' "
                    f"(author={identity.author_surname}, year={identity.year}) "
                    f"but got no hits. Try different phrasings of the "
                    f"canonical title or alternative author/year."
                )
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
        if resolved:
            candidates: list[SearchCandidate] = []
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
                        finalize=False,
                    ),
                )
            writer({"event": "search_results", "candidates": candidates})

        prose = await synthesize_prose(
            resolved,
            not_found,
            user_message=state["user_message"],
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
# paper_qa subgraph
# ---------------------------------------------------------------------------


def build_paper_qa_subgraph(deps: ResearchDeps) -> Any:
    """Compile the paper_qa branching subgraph.

    Topology::

        START → pq_resolve
        pq_resolve → conditional_edges → {
            "empty":   pq_empty       → END
            "single":  pq_single      → END
            "map":     pq_map         → pq_synthesize → END
        }
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
        if n == 0:
            return "empty"
        if n == 1:
            return "single"
        return "map"

    async def _pq_empty(state: AgentState) -> AgentState:
        return {
            **state,
            "final_response": (
                "No references are enabled for this session. Add a paper "
                "to the Reference Sources panel first, then ask again."
            ),
        }

    async def _pq_single(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        paper = state["pq_papers"][0]
        collected: list[str] = []
        final_only: str | None = None
        async for item in _paper_qa_single_stream(
            paper=paper,
            user_message=state["user_message"],
            adapter=deps.adapter,
            tracer=deps.tracer,
            model=deps.paper_qa_model,
            retriever=deps.retriever,
            conn=deps.conn,
            state=state,
            **_kwargs(deps),
        ):
            if isinstance(item, FinalOnlyMessage):
                final_only = item.content
            else:
                writer({"event": "token", "text": item})
                collected.append(item)
        if final_only is not None:
            return {**state, "final_response": final_only}
        return {**state, "final_response": "".join(collected)}

    async def _pq_map(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        papers = list(state["pq_papers"])
        run_id: int = state["run_id"]
        last_step = int(state.get("ps_last_step_index", -1))
        lock = asyncio.Lock()

        async def _one_with_emit(
            pid: int, title: str,
        ) -> tuple[int, str, list[Any], str]:
            result = await _paper_qa_map_one(
                pid=pid,
                title=title,
                user_message=state["user_message"],
                adapter=deps.adapter,
                tracer=deps.tracer,
                model=deps.paper_qa_model,
                retriever=deps.retriever,
                conn=deps.conn,
                **_kwargs(deps),
            )
            # Drain any rows written since the last emission and emit them
            # immediately. The lock prevents two concurrent tasks from
            # claiming the same row twice.
            async with lock:
                nonlocal last_step
                recs = await drain_tool_calls_since(deps.conn, run_id, last_step)
                for rec in recs:
                    writer({"event": "tool_step", "record": rec})
                    last_step = rec["step_index"]
            return result

        results = list(
            await asyncio.gather(
                *[_one_with_emit(pid, title) for pid, title in papers],
            ),
        )
        return {**state, "pq_per_paper": results, "ps_last_step_index": last_step}

    async def _pq_synthesize(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        per_paper = state.get("pq_per_paper") or []
        # If every paper returned no chunks, short-circuit.
        if all(not chunks for _, _, chunks, _ in per_paper):
            return {
                **state,
                "final_response": (
                    "No relevant chunks were found in the enabled references."
                ),
            }
        collected: list[str] = []
        async for tok in _paper_qa_synthesize_stream(
            per_paper=per_paper,
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
    g.add_node("pq_single", _pq_single)
    g.add_node("pq_map", _pq_map)
    g.add_node("pq_synthesize", _pq_synthesize)
    g.add_edge(START, "pq_resolve")
    g.add_conditional_edges(
        "pq_resolve",
        _pq_branch,
        {"empty": "pq_empty", "single": "pq_single", "map": "pq_map"},
    )
    g.add_edge("pq_empty", END)
    g.add_edge("pq_single", END)
    g.add_edge("pq_map", "pq_synthesize")
    g.add_edge("pq_synthesize", END)
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
