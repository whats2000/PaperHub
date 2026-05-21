"""Research Agent helpers — paper_qa only.

paper_search has been decomposed into paperhub.agents.research_pipeline
(v2.7). This module retains the **paper_qa** building blocks the
LangGraph subgraph (paperhub.agents.research_graph) wires together,
plus a few wire-shaped types (FinalOnlyMessage, ToolStepYield,
SearchCandidate, SearchResultsYield) that the chat layer
imports as the SSE-event payload contract.

v2.10: the dense-RAG map-reduce helpers (_paper_qa_map_one,
_paper_qa_synthesize_stream, _paper_qa_map_reduce, _paper_qa_single_paper,
_paper_qa_single_stream, _K_PER_PAPER) have been replaced with the
agentic-hierarchical pipeline. The new ``paper_qa_finalize`` streaming
helper reads raw ``PerPaperPicks`` from per-paper subagents and streams
the finalizer LLM answer over them.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from paperhub.agents.state import AgentState, response_language
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


@dataclass(frozen=True)
class FinalOnlyMessage:
    """Yielded by paper_qa_stream when the early-exit message should be sent
    as a single 'final' SSE event without any 'token' events. Used for the
    empty-references and empty-retrieved cases."""

    content: str


@dataclass(frozen=True)
class ToolStepYield:
    """Forwarded by chat.py to the SSE wire as a 'tool_step' event."""

    record: dict[str, Any]


@dataclass
class SearchCandidate:
    """One Add-as-reference card. v2.7 builds these deterministically in
    research_graph.ps_finalize from the resolved ResolvedPaper set —
    the LLM never authors them."""

    paper_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    arxiv_id: str | None = None
    has_open_pdf: bool = False
    reason: str = ""
    finalize: bool = False
    auto_added: bool = False
    papers_id: int | None = None
    error: str | None = None
    already_in_session: bool = False


@dataclass(frozen=True)
class SearchResultsYield:
    """Yielded by chat.py to the SSE wire as a 'search_results' event."""

    candidates: list[SearchCandidate]


async def _resolve_enabled_papers(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    tracer: Tracer,
) -> list[tuple[int, str]]:
    """Resolve (paper_content_id, title) for every enabled paper in the session.

    Wrapped in a ``paper_qa:resolve`` tracer step for FR-09. Returned shape is
    consumed by the LangGraph paper_qa nodes (count-branch + single / map).
    """
    async with tracer.step(
        agent="research", tool="paper_qa:resolve", model=None,
    ) as step:
        step.record_args({"session_id": session_id})
        async with conn.execute(
            "SELECT pc.id, pc.title FROM papers p "
            "JOIN paper_content pc ON pc.id = p.paper_content_id "
            "WHERE p.session_id = ? AND p.enabled = 1 "
            "ORDER BY p.added_at",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        enabled_papers: list[tuple[int, str]] = [
            (int(r[0]), str(r[1]) if r[1] else f"Paper {r[0]}") for r in rows
        ]
        step.record_result(
            {"enabled": [{"id": i, "title": t} for i, t in enabled_papers]},
        )
    return enabled_papers


async def paper_qa_finalize(
    *,
    per_paper_picks: list[Any],  # list[PerPaperPicks]
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    state: AgentState,
    **adapter_kwargs: Any,
) -> AsyncIterator[str]:
    """Stream the finalizer LLM over collected PerPaperPicks (v2.10).

    Each ``PerPaperPicks`` carries the raw chunks that the per-paper subagent
    cited, plus a brief rationale. The finalizer sees chunk text directly —
    no intermediate analyst prose — which preserves ``[chunk:<id>]`` markers
    verbatim in the output for the Citation Canvas.

    Tracer step name: ``paper_qa:finalize``.
    """
    # Build per_paper_block in Python (not by the LLM).
    parts: list[str] = []
    for pp in per_paper_picks:
        chunks_block = "\n\n".join(
            f"[chunk:{c.chunk_id}]\n{c.text}" for c in pp.picked_chunks
        ) or "(no chunks cited)"
        parts.append(
            f'## "{pp.title}"\n'
            f"Subagent rationale: {pp.rationale}\n"
            f"Relevant chunks:\n{chunks_block}"
        )
    per_paper_block = "\n\n---\n\n".join(parts)

    async with tracer.step(
        agent="research", tool="paper_qa:finalize", model=model,
    ) as step:
        step.record_args({
            "n_papers": len(per_paper_picks),
            "n_chunks": sum(len(p.picked_chunks) for p in per_paper_picks),
        })
        collected: list[str] = []
        async for tok in adapter.stream(
            slot="paper_qa_synthesize/v2",
            variables={
                "user_message": user_message,
                "per_paper_block": per_paper_block,
                "response_language": response_language(state),
            },
            model=model,
            history=state.get("history"),
            **adapter_kwargs,
        ):
            collected.append(tok)
            yield tok
        step.record_result({"length": sum(len(c) for c in collected)})


async def paper_qa_stream(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Any,
    conn: aiosqlite.Connection,
    **adapter_kwargs: Any,
) -> AsyncIterator[str | FinalOnlyMessage]:
    """Stream paper_qa tokens (legacy async-generator façade).

    Retained for the test surface and for callers that don't want to
    drive the LangGraph subgraph directly. Behaviour:

    N=0: yields FinalOnlyMessage (no enabled papers).
    N≥1: resolves enabled papers; yields FinalOnlyMessage for the
         no-papers case — does NOT drive the full agentic subagent loop
         (that path requires the full ResearchDeps / compiled graph).
         Tests that need end-to-end coverage should drive
         ``build_paper_qa_subgraph`` directly.
    """
    enabled_papers = await _resolve_enabled_papers(
        conn, session_id=state["session_id"], tracer=tracer,
    )

    if not enabled_papers:
        yield FinalOnlyMessage(
            "No references are enabled for this session. Add a paper to the "
            "Reference Sources panel first, then ask again.",
        )
