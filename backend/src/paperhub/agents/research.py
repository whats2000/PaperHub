"""Research Agent: paper_search tool-calling loop (SRS v2.4) + paper_qa stream.

v2.4 paper_search is read-only: the LLM may call search_library /
search_semantic_scholar / find_related_papers, then ends the turn with a
``json:candidates`` fenced block carrying 3-5 picks. Up to 2 picks may be
flagged ``finalize: true``; the chat endpoint auto-attaches those. Suggested-
only picks are NEVER downloaded.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

import aiosqlite
import litellm

from paperhub.agents.research_tools import (
    TOOL_SCHEMAS,
    find_related_papers_dispatch,
    search_library_dispatch,
    search_semantic_scholar_dispatch,
)
from paperhub.agents.state import AgentState
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import Tracer


@dataclass(frozen=True)
class FinalOnlyMessage:
    """Yielded by paper_qa_stream when the early-exit message should be sent
    as a single 'final' SSE event without any 'token' events. Used for the
    empty-references and empty-retrieved cases."""

    content: str


@dataclass(frozen=True)
class ToolStepYield:
    """Yielded by paper_search after each tracer.step closes so the chat
    endpoint can forward as a tool_step SSE event in real time."""

    record: dict[str, Any]  # the just-persisted tool_calls row


@dataclass
class SearchCandidate:
    """A single shortlisted paper, surfaced as a SearchResultList card.

    Mutable so the chat layer can ``replace``-style update ``finalize``,
    ``auto_added``, ``papers_id``, ``error``, ``already_in_session`` after
    enforcing the finalize cap + dispatching the auto-attach.
    """

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
    """Yielded by paper_search after parsing the agent's final
    ``json:candidates`` fenced block. The chat layer enforces the
    finalize cap, dispatches auto-attach for finalize=True picks, then
    emits the enriched list as a ``search_results`` SSE event."""

    candidates: list[SearchCandidate]


# v2.4: applies to search_semantic_scholar (not find_related_papers, which
# is precise navigation, not free-text search and stays uncapped).
MAX_EXTERNAL_SEARCH_CALLS_PER_TURN = 3
# Hard ceiling: search_library + 3 × search_semantic_scholar + a couple of
# find_related_papers + slack for clarification turns.
MAX_TOOL_ITERATIONS = 8


CANDIDATES_BLOCK_RE = re.compile(r"```json:candidates\s*\n(.*?)\n```", re.DOTALL)


def _extract_candidates(
    final_text: str,
    recent_results: dict[str, dict[str, Any]],
) -> tuple[str, list[SearchCandidate]]:
    """Strip the ``json:candidates`` fenced block from ``final_text`` and
    parse the picks. Picks whose ``paper_id`` wasn't surfaced by an earlier
    tool call (in ``recent_results``) are dropped defensively — the agent
    occasionally hallucinates IDs.

    Returns ``(cleaned_text, candidates)``. When the block is missing,
    returns ``(final_text, [])`` so the caller can still emit a final
    message rather than crash.
    """
    m = CANDIDATES_BLOCK_RE.search(final_text)
    if not m:
        return final_text, []
    try:
        raw_picks = json.loads(m.group(1))
    except json.JSONDecodeError:
        # Malformed JSON — be tolerant; the agent likely meant well.
        return CANDIDATES_BLOCK_RE.sub("", final_text).strip(), []
    if not isinstance(raw_picks, list):
        return CANDIDATES_BLOCK_RE.sub("", final_text).strip(), []
    cleaned_text = CANDIDATES_BLOCK_RE.sub("", final_text).strip()
    candidates: list[SearchCandidate] = []
    for pick in raw_picks:
        if not isinstance(pick, dict):
            continue
        pid = pick.get("paper_id")
        if not isinstance(pid, str):
            continue
        meta = recent_results.get(pid)
        if meta is None:
            # The agent picked something we didn't return — drop it.
            continue
        candidates.append(
            SearchCandidate(
                paper_id=pid,
                title=str(meta.get("title", "")),
                authors=list(meta.get("authors", []) or []),
                year=meta.get("year"),
                abstract=meta.get("abstract"),
                arxiv_id=meta.get("arxiv_id"),
                has_open_pdf=bool(meta.get("has_open_pdf") or meta.get("open_access_pdf_url")),
                reason=str(pick.get("reason", "")),
                finalize=bool(pick.get("finalize", False)),
            ),
        )
    return cleaned_text, candidates


def _index_library_hit(hit: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """search_library returns a paper_content_id; map to ``library:<id>``."""
    pcid = hit["paper_content_id"]
    pid = f"library:{pcid}"
    return pid, {
        "title": hit.get("title", ""),
        "authors": [],
        "year": hit.get("year"),
        "abstract": hit.get("abstract"),
        "arxiv_id": hit.get("arxiv_id"),
        "has_open_pdf": False,
    }


def _index_ss_hit(hit: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    pid = hit["paper_id"]  # already prefixed by the dispatcher
    return pid, {
        "title": hit.get("title", ""),
        "authors": list(hit.get("authors", []) or []),
        "year": hit.get("year"),
        "abstract": hit.get("abstract"),
        "arxiv_id": hit.get("arxiv_id"),
        "has_open_pdf": bool(hit.get("has_open_pdf")),
    }


def _index_related_hit(hit: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """find_related returns RelatedPaper dicts with arxiv_id when available.
    Prefer ``arxiv:`` prefix; skip entries without an arxiv ID (no stable
    paper_id we can issue without the SS paperId)."""
    arxiv_id = hit.get("arxiv_id")
    if not arxiv_id:
        return None
    pid = f"arxiv:{arxiv_id}"
    return pid, {
        "title": hit.get("title", ""),
        "authors": list(hit.get("authors", []) or []),
        "year": hit.get("year"),
        "abstract": hit.get("abstract"),
        "arxiv_id": arxiv_id,
        "has_open_pdf": False,
    }


async def _references_block(
    conn: aiosqlite.Connection, session_id: int,
) -> tuple[int, str]:
    async with conn.execute(
        "SELECT pc.arxiv_id, pc.title, pc.year, pc.abstract "
        "FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 "
        "ORDER BY p.added_at",
        (session_id,),
    ) as cur:
        rows = list(await cur.fetchall())
    if not rows:
        return 0, "(none — this session has no references yet)"
    lines: list[str] = []
    for r in rows:
        aid, title, year, abstract = r
        head = (
            f"- [arxiv:{aid}] {title} ({year or 'n.d.'})"
            if aid
            else f"- {title} ({year or 'n.d.'})"
        )
        snippet = (abstract or "")[:200].replace("\n", " ")
        ellipsis = "…" if abstract and len(abstract) > 200 else ""
        lines.append(f"{head}\n  abstract: {snippet}{ellipsis}")
    return len(rows), "\n".join(lines)


async def paper_search(
    state: AgentState,
    *,
    adapter: LlmAdapter | None,  # kept for interface parity; uses litellm directly
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    pipeline: PaperPipeline,  # noqa: ARG001 — kept for interface parity (chat layer attaches)
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> AsyncIterator[ToolStepYield | SearchResultsYield | FinalOnlyMessage]:
    """v2.4 read-only tool-calling loop.

    Yields:
      - ToolStepYield after each tracer.step closes (real-time trace updates).
      - SearchResultsYield(candidates) parsed from the agent's final
        ``json:candidates`` block, BEFORE the FinalOnlyMessage so the chat
        layer can enrich + emit the search_results SSE event in order.
      - FinalOnlyMessage(prose only — the json block has been stripped).
    """
    del adapter  # interface parity only
    user_message = state["user_message"]
    session_id = state["session_id"]
    history = state.get("history") or []

    n_refs, refs_block = await _references_block(conn, session_id)
    reg = registry or PromptRegistry()
    prompt = reg.get("paper_search/v1")
    system = prompt.system
    user = prompt.user_template.format(
        n_refs=n_refs, references_block=refs_block, user_message=user_message,
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user})

    run_id: int = state["run_id"]
    last_yielded_step = -1
    external_search_calls = 0
    # Accumulator: paper_id → metadata, keyed by the prefixed id the agent will
    # quote back in its ``json:candidates`` block.
    recent_results: dict[str, dict[str, Any]] = {}

    for iteration in range(MAX_TOOL_ITERATIONS):
        async with tracer.step(
            agent="research", tool="paper_search:plan", model=model,
        ) as step:
            step.record_args(
                {"iteration": iteration, "messages_len": len(messages)},
            )
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                **litellm_kwargs,
            )
            msg = response["choices"][0]["message"]
            step.record_result(
                {
                    "had_tool_calls": bool(msg.get("tool_calls")),
                    "content_len": len(msg.get("content") or ""),
                },
            )

        # Yield the plan step that just closed.
        for rec in await drain_tool_calls_since(conn, run_id, last_yielded_step):
            yield ToolStepYield(record=rec)
            last_yielded_step = rec["step_index"]

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # Final response — clarification or shortlist.
            final_text = str(msg.get("content") or "(no response)")
            cleaned_text, candidates = _extract_candidates(final_text, recent_results)
            if candidates:
                yield SearchResultsYield(candidates=candidates)
            yield FinalOnlyMessage(cleaned_text)
            return

        # Append the assistant turn that requested the tools, then dispatch each.
        messages.append(
            {
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            },
        )

        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            result: Any
            async with tracer.step(
                agent="research", tool=f"paper_search:{name}", model=None,
            ) as step:
                step.record_args(args)
                try:
                    if name == "search_library":
                        lib_hits = [
                            asdict(h)
                            for h in await search_library_dispatch(
                                conn=conn, session_id=session_id, **args,
                            )
                        ]
                        for hit in lib_hits:
                            pid, meta = _index_library_hit(hit)
                            recent_results[pid] = meta
                        # Add the prefixed paper_id to each row so the LLM
                        # can quote it back verbatim in json:candidates.
                        result = [
                            {**h, "paper_id": f"library:{h['paper_content_id']}"}
                            for h in lib_hits
                        ]
                    elif name == "search_semantic_scholar":
                        if external_search_calls >= MAX_EXTERNAL_SEARCH_CALLS_PER_TURN:
                            result = {
                                "error": "external_search_call_cap_reached",
                                "cap": MAX_EXTERNAL_SEARCH_CALLS_PER_TURN,
                            }
                        else:
                            external_search_calls += 1
                            ss_hits = [
                                asdict(h)
                                for h in await search_semantic_scholar_dispatch(**args)
                            ]
                            for hit in ss_hits:
                                pid, meta = _index_ss_hit(hit)
                                recent_results[pid] = meta
                            result = ss_hits
                    elif name == "find_related_papers":
                        related = await find_related_papers_dispatch(**args)
                        for hit in related:
                            indexed = _index_related_hit(hit)
                            if indexed is not None:
                                pid, meta = indexed
                                recent_results[pid] = meta
                        result = related
                    else:
                        result = {"error": f"unknown_tool:{name}"}
                    step.record_result(
                        {
                            "summary": result
                            if isinstance(result, dict)
                            else {"count": len(result)},
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc), "tool": name}
                    step.record_result({"error": str(exc)})
                    step.mark_error(str(exc))

            # Yield the tool-dispatch step that just closed.
            for rec in await drain_tool_calls_since(conn, run_id, last_yielded_step):
                yield ToolStepYield(record=rec)
                last_yielded_step = rec["step_index"]

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": json.dumps(result, default=str),
                },
            )

    yield FinalOnlyMessage(
        "I've reached the tool-call limit for this turn. "
        "Try asking again with a more specific question.",
    )


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
    """Stream paper_qa tokens.

    Workflow: resolve enabled_paper_content_ids → retrieve → rerank → format
    chunk context → stream LLM answer with [chunk:<id>] markers.
    """
    user_message = state["user_message"]
    session_id = state["session_id"]

    async with tracer.step(
        agent="research", tool="paper_qa:resolve", model=None,
    ) as step:
        step.record_args({"session_id": session_id})
        async with conn.execute(
            "SELECT paper_content_id FROM papers "
            "WHERE session_id = ? AND enabled = 1",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        enabled_ids = [int(r[0]) for r in rows]
        step.record_result({"enabled_paper_content_ids": enabled_ids})

    if not enabled_ids:
        yield FinalOnlyMessage(
            "No references are enabled for this session. Add a paper to the "
            "Reference Sources panel first, then ask again.",
        )
        return

    placeholders = ",".join("?" * len(enabled_ids))
    async with conn.execute(
        f"SELECT COUNT(*) FROM chunks WHERE paper_content_id IN ({placeholders})",  # noqa: S608
        enabled_ids,
    ) as cur:
        row = await cur.fetchone()
    corpus_size = int(row[0]) if row else 0

    async with tracer.step(
        agent="research", tool="paper_qa:retrieve", model=None,
    ) as step:
        step.record_args({"query": user_message, "corpus_size": corpus_size})
        retrieved = retriever.retrieve(
            user_message,
            enabled_paper_content_ids=enabled_ids,
            corpus_size=corpus_size,
            top_k=10,
        )
        step.record_result({"chunk_ids": [r.chunk_id for r in retrieved]})

    if not retrieved:
        yield FinalOnlyMessage("No relevant chunks were found in the enabled references.")
        return

    chunks_context = "\n\n".join(
        f"[chunk:{r.chunk_id}] (paper {r.paper_content_id})\n{r.text}"
        for r in retrieved
    )

    async with tracer.step(
        agent="research", tool="paper_qa:generate", model=model,
    ) as step:
        step.record_args({"chunk_count": len(retrieved)})
        collected: list[str] = []
        async for token in adapter.stream(
            slot="paper_qa/v1",
            variables={
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
