"""Research Agent helpers (SRS v2.4): node bodies for the paper_search +
paper_qa LangGraph subgraphs.

This module hosts the **building blocks** the multi-node Research subgraph
(``paperhub.agents.research_graph``) wires together as graph edges. Each
helper is a focused step:

* paper_search:
    - ``_references_block`` / paper_search prompt seed
    - ``_extract_candidates`` (parses the final ``json:candidates`` block)
    - ``_index_library_hit`` / ``_index_ss_hit`` / ``_index_related_hit``
* paper_qa:
    - ``_resolve_enabled_papers`` (resolve enabled refs for the session)
    - ``_paper_qa_single_stream`` (N=1: retrieve + generate)
    - ``_paper_qa_map_one`` (map step: one paper → analysis)
    - ``_paper_qa_synthesize_stream`` (reduce step: merge analyses)

The "umbrella" async generators ``paper_search`` and ``paper_qa_stream``
that lived here in Plan C v3 are intentionally retained as **legacy
façades** — they compose the helpers in series and let the existing
test suite (``test_research_paper_search.py`` /
``test_research_paper_qa.py``) keep exercising the same surface without
needing to spin up a LangGraph. New control-flow work happens in the
subgraph; these façades exist only so the test seam remains.

v2.4 paper_search is read-only: the LLM may call search_library /
search_semantic_scholar / find_related_papers, then ends the turn with a
``json:candidates`` fenced block carrying 3-5 picks. Up to 2 picks may be
flagged ``finalize: true``; the chat endpoint auto-attaches those. Suggested-
only picks are NEVER downloaded.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
import litellm

from paperhub.agents.research_tools import build_tool_schemas
from paperhub.agents.state import AgentState
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.mcp.registry import MCPRegistry
from paperhub.pipelines.paper_pipeline import PaperPipeline
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


# v2.6: applies to `papers.search_semantic_scholar` + every `web.*` tool
# (search, fetch) — discovery is rate-limited, navigation isn't.
# `papers.search_library` and `papers.find_related_papers` remain uncapped:
# library lookup is local + cheap, citation-graph navigation is precise.
MAX_EXTERNAL_DISCOVERY_CALLS_PER_TURN = 3
# Hard ceiling: search_library + 3 × external discovery + a couple of
# find_related_papers + slack for clarification turns.
MAX_TOOL_ITERATIONS = 8

# Tool names (un-namespaced) that count toward the external-discovery cap.
# Inspected against the suffix after the first ``.`` in the namespaced
# tool name: ``papers.search_semantic_scholar`` → ``search_semantic_scholar``.
_CAPPED_PAPERS_TOOLS = frozenset({"search_semantic_scholar"})


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


async def _build_paper_search_messages(
    *,
    state: AgentState,
    conn: aiosqlite.Connection,
    registry: PromptRegistry | None = None,
) -> list[dict[str, Any]]:
    """Build the initial LLM message list for a paper_search turn.

    Composes ``system`` (from paper_search/v1 prompt), the prior chat
    ``history`` (already-formatted role/content dicts), and the rendered
    ``user`` prompt that includes the session's enabled references block.

    Extracted from the legacy ``paper_search`` async-generator so the
    LangGraph ``ps_plan`` node can build state["ps_messages"] on the first
    iteration without re-implementing the seed logic.
    """
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
    return messages


async def _paper_search_plan_step(
    *,
    messages: list[dict[str, Any]],
    tracer: Tracer,
    model: str,
    iteration: int,
    mcp_registry: MCPRegistry,
    **litellm_kwargs: Any,
) -> dict[str, Any]:
    """Run one paper_search:plan tracer step + litellm.acompletion call.

    Returns the assistant message (``dict`` with ``content`` and possibly
    ``tool_calls``). The tracer step row is persisted as a side effect; the
    caller drains it via ``drain_tool_calls_since``.

    The LLM tool palette is sourced from the MCP registry — every name
    arrives namespaced (``papers.search_library``, ``web.search``, …).
    """
    tools = await build_tool_schemas(mcp_registry)
    async with tracer.step(
        agent="research", tool="paper_search:plan", model=model,
    ) as step:
        step.record_args(
            {"iteration": iteration, "messages_len": len(messages)},
        )
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            **litellm_kwargs,
        )
        msg: dict[str, Any] = response["choices"][0]["message"]
        step.record_result(
            {
                "had_tool_calls": bool(msg.get("tool_calls")),
                "content_len": len(msg.get("content") or ""),
            },
        )
    return msg


def _counts_against_discovery_cap(name: str) -> bool:
    """Return True iff ``name`` (namespaced ``<server>.<tool>``) counts
    toward :data:`MAX_EXTERNAL_DISCOVERY_CALLS_PER_TURN`.

    Every ``web.*`` tool counts (search, fetch — both incur external HTTP
    plus latency); ``papers.search_semantic_scholar`` counts (external SS
    API), but ``papers.search_library`` / ``papers.find_related_papers``
    do not (local FTS / precise citation navigation).
    """
    if "." not in name:
        return False
    server, tool = name.split(".", 1)
    if server == "web":
        return True
    if server == "papers":
        return tool in _CAPPED_PAPERS_TOOLS
    return False


def _index_results_into_recent(
    name: str,
    result: Any,
    recent_results: dict[str, dict[str, Any]],
) -> None:
    """Update ``recent_results`` for the namespaced tool's result shape.

    The agent's ``json:candidates`` block resolves picks against this map.
    ``papers.*`` results carry the schemas the dispatchers return; ``web.*``
    hits are NOT indexed (no stable paper_id surface) — they exist purely
    to broaden the agent's context.
    """
    if not isinstance(result, list):
        return
    if "." not in name:
        return
    server, tool = name.split(".", 1)
    if server != "papers":
        return  # web.* and other servers are context-only.
    if tool == "search_library":
        for hit in result:
            if not isinstance(hit, dict):
                continue
            pid, meta = _index_library_hit(hit)
            recent_results[pid] = meta
    elif tool == "search_semantic_scholar":
        for hit in result:
            if not isinstance(hit, dict):
                continue
            pid, meta = _index_ss_hit(hit)
            recent_results[pid] = meta
    elif tool == "find_related_papers":
        for hit in result:
            if not isinstance(hit, dict):
                continue
            indexed = _index_related_hit(hit)
            if indexed is not None:
                pid, meta = indexed
                recent_results[pid] = meta


async def _dispatch_paper_search_tool_call(
    *,
    call: dict[str, Any],
    tracer: Tracer,
    conn: aiosqlite.Connection,  # noqa: ARG001 — kept for facade-parity / future
    session_id: int,  # noqa: ARG001 — registry threads session via headers
    external_discovery_calls: int,
    recent_results: dict[str, dict[str, Any]],
    registry: MCPRegistry,
) -> tuple[Any, int]:
    """Dispatch a single tool call through the MCP registry.

    Updates ``recent_results`` in place. Returns
    ``(result, new_discovery_count)``. The result is JSON-serialisable;
    the caller stitches it into the LLM message list as a ``tool`` role
    message. Tracer step is named ``paper_search:<namespaced_name>``
    (e.g. ``paper_search:papers.search_library``).

    Cap semantics: ``web.*`` + ``papers.search_semantic_scholar`` count
    toward :data:`MAX_EXTERNAL_DISCOVERY_CALLS_PER_TURN`. A capped call
    is rejected before reaching the registry (its slot remains free for
    the next allowed call).
    """
    name = call["function"]["name"]
    args = json.loads(call["function"]["arguments"] or "{}")
    result: Any
    new_count = external_discovery_calls
    async with tracer.step(
        agent="research", tool=f"paper_search:{name}", model=None,
    ) as step:
        step.record_args(args)
        try:
            counts = _counts_against_discovery_cap(name)
            if counts and external_discovery_calls >= MAX_EXTERNAL_DISCOVERY_CALLS_PER_TURN:
                result = {
                    "error": "external_discovery_call_cap_reached",
                    "cap": MAX_EXTERNAL_DISCOVERY_CALLS_PER_TURN,
                }
            else:
                if counts:
                    new_count = external_discovery_calls + 1
                result = await registry.call(name, args)
                _index_results_into_recent(name, result, recent_results)
            step.record_result(
                {
                    "summary": result
                    if isinstance(result, dict)
                    else {"count": len(result) if hasattr(result, "__len__") else 1},
                },
            )
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc), "tool": name}
            step.record_result({"error": str(exc)})
            step.mark_error(str(exc))
    return result, new_count


async def paper_search(
    state: AgentState,
    *,
    adapter: LlmAdapter | None,  # kept for interface parity; uses litellm directly
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    pipeline: PaperPipeline,  # noqa: ARG001 — kept for interface parity (chat layer attaches)
    mcp_registry: MCPRegistry,
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> AsyncIterator[ToolStepYield | SearchResultsYield | FinalOnlyMessage]:
    """Legacy async-generator façade over the paper_search helpers.

    Plan C v4 retired this as the orchestration source — control flow is
    now expressed as graph edges in ``research_graph.build_paper_search_subgraph``.
    This generator is kept because two test files still drive it directly:

      - ``test_research_paper_search.py`` (5 cases) — easier to assert on a
        flat async iterator than to spin up a LangGraph;
      - ``test_chat_sse.py`` (paper_search cases) — monkeypatch
        ``chat_module.paper_search`` with a fake generator.

    Implementation composes the same helpers the subgraph wires together,
    so behaviour and tracer-step shape stay identical.
    """
    del adapter  # interface parity only
    messages = await _build_paper_search_messages(
        state=state, conn=conn, registry=registry,
    )
    session_id = state["session_id"]
    run_id: int = state["run_id"]
    last_yielded_step = -1
    external_discovery_calls = 0
    # Accumulator: paper_id → metadata, keyed by the prefixed id the agent will
    # quote back in its ``json:candidates`` block.
    recent_results: dict[str, dict[str, Any]] = {}

    for iteration in range(MAX_TOOL_ITERATIONS):
        msg = await _paper_search_plan_step(
            messages=messages, tracer=tracer, model=model,
            iteration=iteration, mcp_registry=mcp_registry,
            **litellm_kwargs,
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
            result, external_discovery_calls = await _dispatch_paper_search_tool_call(
                call=call, tracer=tracer, conn=conn, session_id=session_id,
                external_discovery_calls=external_discovery_calls,
                recent_results=recent_results,
                registry=mcp_registry,
            )

            # Yield the tool-dispatch step that just closed.
            for rec in await drain_tool_calls_since(conn, run_id, last_yielded_step):
                yield ToolStepYield(record=rec)
                last_yielded_step = rec["step_index"]

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["function"]["name"],
                    "content": json.dumps(result, default=str),
                },
            )

    yield FinalOnlyMessage(
        "I've reached the tool-call limit for this turn. "
        "Try asking again with a more specific question.",
    )


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
