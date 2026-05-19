"""Research Agent helpers — paper_qa only.

paper_search has been decomposed into paperhub.agents.research_pipeline
(v2.7). This module retains the **paper_qa** building blocks the
LangGraph subgraph (paperhub.agents.research_graph) wires together,
plus a few wire-shaped types (FinalOnlyMessage, ToolStepYield,
SearchCandidate, SearchResultsYield) that the chat layer
imports as the SSE-event payload contract.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from paperhub.agents.state import AgentState
from paperhub.llm.adapter import LlmAdapter
from paperhub.rag.retriever import RetrievedChunk, Retriever
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


async def paper_qa_stream(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Retriever,
    conn: aiosqlite.Connection,
    **adapter_kwargs: Any,
) -> AsyncIterator[str | FinalOnlyMessage]:
    """Stream paper_qa tokens (legacy async-generator façade).

    Retained for tests that exercise the full flow as a single generator.
    The chat.py request path now runs through the LangGraph research subgraph
    in ``paperhub.agents.research_graph``; this helper composes the same node
    primitives in series.

    N=0: yields FinalOnlyMessage (no enabled papers).
    N=1: single-paper path — retrieve + generate, citing paper by title.
    N>=2: map-reduce — parallel per-paper retrieval + analysis, then synthesis.
    """
    user_message = state["user_message"]

    enabled_papers = await _resolve_enabled_papers(
        conn, session_id=state["session_id"], tracer=tracer,
    )

    if not enabled_papers:
        yield FinalOnlyMessage(
            "No references are enabled for this session. Add a paper to the "
            "Reference Sources panel first, then ask again.",
        )
        return

    if len(enabled_papers) == 1:
        async for tok in _paper_qa_single_stream(
            paper=enabled_papers[0],
            user_message=user_message,
            adapter=adapter,
            tracer=tracer,
            model=model,
            retriever=retriever,
            conn=conn,
            state=state,
            **adapter_kwargs,
        ):
            yield tok
        return

    # N >= 2: map-reduce path.
    async for tok in _paper_qa_map_reduce(
        enabled_papers,
        user_message=user_message,
        adapter=adapter,
        tracer=tracer,
        model=model,
        retriever=retriever,
        conn=conn,
        state=state,
        **adapter_kwargs,
    ):
        yield tok


async def _paper_qa_single_stream(
    *,
    paper: tuple[int, str],
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Retriever,
    conn: aiosqlite.Connection,
    state: AgentState,
    **adapter_kwargs: Any,
) -> AsyncIterator[str | FinalOnlyMessage]:
    """Single-paper path: retrieve top-k chunks, stream LLM citing by title.

    Yields a single ``FinalOnlyMessage`` when no chunks are retrieved (the
    "empty corpus" sentinel); otherwise yields successive token strings.
    """
    pid, title = paper

    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?", (pid,),
    ) as cur:
        count_row = await cur.fetchone()
    corpus_size = int(count_row[0]) if count_row else 0

    async with tracer.step(
        agent="research", tool="paper_qa:retrieve", model=None,
    ) as step:
        step.record_args({"query": user_message, "corpus_size": corpus_size})
        retrieved = retriever.retrieve(
            user_message,
            enabled_paper_content_ids=[pid],
            corpus_size=corpus_size,
            top_k=10,
        )
        step.record_result({"chunk_ids": [r.chunk_id for r in retrieved]})

    if not retrieved:
        yield FinalOnlyMessage("No relevant chunks were found in the enabled references.")
        return

    chunks_context = "\n\n".join(
        f"[chunk:{r.chunk_id}]\n{r.text}" for r in retrieved
    )

    async with tracer.step(
        agent="research", tool="paper_qa:generate", model=model,
    ) as step:
        step.record_args({"chunk_count": len(retrieved)})
        collected: list[str] = []
        async for token in adapter.stream(
            slot="paper_qa/v1",
            variables={
                "title": title,
                "user_message": user_message,
                "chunks_context": chunks_context,
            },
            model=model,
            history=state.get("history"),
            **adapter_kwargs,
        ):
            collected.append(token)
            yield token
        step.record_result({"length": sum(len(c) for c in collected)})


# Backwards-compat alias used by the legacy async-generator paper_qa_stream
# (kept for test surface). Identical to ``_paper_qa_single_stream`` modulo the
# positional ``paper`` argument.
async def _paper_qa_single_paper(
    paper: tuple[int, str],
    *,
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Retriever,
    conn: aiosqlite.Connection,
    state: AgentState,
    **adapter_kwargs: Any,
) -> AsyncIterator[str | FinalOnlyMessage]:
    async for tok in _paper_qa_single_stream(
        paper=paper,
        user_message=user_message,
        adapter=adapter,
        tracer=tracer,
        model=model,
        retriever=retriever,
        conn=conn,
        state=state,
        **adapter_kwargs,
    ):
        yield tok


# Chunks per paper in the map step — balance between context size and signal.
_K_PER_PAPER = 5


async def _paper_qa_map_one(
    *,
    pid: int,
    title: str,
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Retriever,
    conn: aiosqlite.Connection,
    **adapter_kwargs: Any,
) -> tuple[int, str, list[RetrievedChunk], str]:
    """Single map step: retrieve top-k chunks for one paper, run the
    per-paper analysis LLM call, return ``(pid, title, chunks, analysis)``.

    The map node in the research subgraph fans these out via ``asyncio.gather``.
    """
    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?", (pid,),
    ) as cur:
        count_row = await cur.fetchone()
    corpus = int(count_row[0]) if count_row else 0

    async with tracer.step(
        agent="research", tool="paper_qa:map", model=model,
    ) as step:
        step.record_args(
            {"paper_content_id": pid, "title": title, "k": _K_PER_PAPER},
        )
        chunks = retriever.retrieve(
            user_message,
            enabled_paper_content_ids=[pid],
            corpus_size=corpus,
            top_k=_K_PER_PAPER,
        )

        if not chunks:
            step.record_result({"chunk_ids": [], "analysis": "(no chunks)"})
            return (pid, title, [], "(no relevant chunks found in this paper)")

        chunks_text = "\n\n".join(
            f"[chunk:{c.chunk_id}]\n{c.text}" for c in chunks
        )
        analysis_text = ""
        async for tok in adapter.stream(
            slot="paper_qa_per_paper/v1",
            variables={
                "title": title,
                "chunks": chunks_text,
                "user_message": user_message,
            },
            model=model,
            history=None,
            **adapter_kwargs,
        ):
            analysis_text += tok
        step.record_result(
            {
                "chunk_ids": [c.chunk_id for c in chunks],
                "analysis_len": len(analysis_text),
            },
        )

    return (pid, title, chunks, analysis_text)


async def _paper_qa_synthesize_stream(
    *,
    per_paper: list[tuple[int, str, list[RetrievedChunk], str]],
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    state: AgentState,
    **adapter_kwargs: Any,
) -> AsyncIterator[str]:
    """Reduce step: stream the synthesizer LLM over per-paper analyses.

    Caller is expected to have already handled the "no chunks across the
    board" short-circuit; this helper assumes at least one paper has chunks.
    """
    per_paper_block = "\n\n---\n\n".join(
        f'## "{title}"\n{analysis}' for _, title, _, analysis in per_paper
    )

    async with tracer.step(
        agent="research", tool="paper_qa:synthesize", model=model,
    ) as step:
        step.record_args(
            {
                "n_papers": len(per_paper),
                "n_chunks": sum(len(c) for _, _, c, _ in per_paper),
            },
        )
        collected: list[str] = []
        async for tok in adapter.stream(
            slot="paper_qa_synthesize/v1",
            variables={
                "user_message": user_message,
                "per_paper_analyses": per_paper_block,
            },
            model=model,
            history=state.get("history"),
            **adapter_kwargs,
        ):
            collected.append(tok)
            yield tok
        step.record_result({"length": sum(len(c) for c in collected)})


async def _paper_qa_map_reduce(
    papers: list[tuple[int, str]],
    *,
    user_message: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    retriever: Retriever,
    conn: aiosqlite.Connection,
    state: AgentState,
    **adapter_kwargs: Any,
) -> AsyncIterator[str | FinalOnlyMessage]:
    """Map-reduce path for N>=2 papers (legacy async-generator façade).

    Fan-out happens via ``asyncio.gather`` over ``_paper_qa_map_one``; the
    reduce step is delegated to ``_paper_qa_synthesize_stream``. Both helpers
    are reused by the LangGraph map / synthesize nodes.
    """
    results: list[tuple[int, str, list[RetrievedChunk], str]] = list(
        await asyncio.gather(
            *[
                _paper_qa_map_one(
                    pid=pid,
                    title=title,
                    user_message=user_message,
                    adapter=adapter,
                    tracer=tracer,
                    model=model,
                    retriever=retriever,
                    conn=conn,
                    **adapter_kwargs,
                )
                for pid, title in papers
            ],
        ),
    )

    # If every paper returned no chunks, short-circuit.
    if all(not chunks for _, _, chunks, _ in results):
        yield FinalOnlyMessage(
            "No relevant chunks were found in the enabled references.",
        )
        return

    async for tok in _paper_qa_synthesize_stream(
        per_paper=results,
        user_message=user_message,
        adapter=adapter,
        tracer=tracer,
        model=model,
        state=state,
        **adapter_kwargs,
    ):
        yield tok
