import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

import aiosqlite
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from paperhub.agents.chitchat import chitchat_stream
from paperhub.agents.graph import CLARIFY_FALLBACK
from paperhub.agents.memory_node import memory_node
from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.agents.research import (
    FinalOnlyMessage,
    SearchCandidate,
    SearchResultsYield,
    ToolStepYield,
)
from paperhub.agents.research_graph import (
    ResearchDeps,
    build_paper_qa_subgraph,
    build_paper_search_subgraph,
)
from paperhub.agents.research_tools import (
    NoIngestibleSourceError,
    add_paper_to_session_dispatch,
)
from paperhub.agents.router import router_node
from paperhub.agents.slide_context import build_slide_context
from paperhub.agents.sql_agent import sql_agent_stream
from paperhub.agents.state import AgentState
from paperhub.agents.stubs import stub_response
from paperhub.agents.style_commands import (
    classify_style_command,
    handle_style_command,
)
from paperhub.api.run_broker import RunBroker, RunHandle
from paperhub.config import Settings, load_settings
from paperhub.db.connection import open_db
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.mcp.client_context import (
    ClientHeadersContext,
    reset_client_headers_context,
    set_client_headers_context,
)
from paperhub.mcp.registry import MCPRegistry
from paperhub.models.events import (
    ErrorEvent,
    FinalEvent,
    RoutingDecisionEvent,
    SearchCandidateModel,
    SearchResultsEvent,
    SessionEvent,
    TokenEvent,
)
from paperhub.pipelines.paper_pipeline import ArxivMetadata, PaperPipeline
from paperhub.tracing.redactor import redact
from paperhub.tracing.tracer import Tracer

router = APIRouter()

# ---------------------------------------------------------------------------
# Resumable streaming (FR-15): a chat turn runs as a backend-owned background
# ``asyncio.Task`` whose SSE events are buffered in a per-run ``RunHandle``;
# ``POST /chat`` returns a thin SUBSCRIBER stream. A client disconnect does NOT
# cancel the run (only the explicit Stop endpoint, A3, cancels).
# ---------------------------------------------------------------------------
broker = RunBroker()
_live_tasks: set[asyncio.Task[Any]] = set()
"""Strong refs to in-flight run tasks so they are not GC'd mid-run."""


# ---------------------------------------------------------------------------
# Yield types used by the subgraph-driving shims below.
# ---------------------------------------------------------------------------


@dataclass
class DeckYield:
    """Emitted by ``report_stream`` when the Report subgraph finishes
    building a deck.  The ``deck`` dict is the raw payload forwarded
    verbatim as the ``deck`` SSE event data."""

    deck: dict[str, Any]


class HistoryEntry(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    session_id: int | None = None
    user_message: str
    history: list[HistoryEntry] = Field(default_factory=list)
    # The slide page currently on screen in the frontend's Slides panel
    # (1-based). The Report Agent's deck-command classifier reads this to
    # resolve "edit this slide" to the visible page. 0 = no deck open.
    current_view_page: int = 0
    # Set by the composer's "Slide" chip when the user wants slide-aware QA.
    # When True the active-slide context block is built and threaded into
    # AgentState so paper_qa can anchor its answer to the visible slide.
    slide_attached: bool = False


async def _ensure_session(conn: aiosqlite.Connection, session_id: int | None) -> int:
    if session_id is not None:
        # The client may hold a backend_session_id (in localStorage) that no
        # longer exists in the DB — a reset workspace, a deleted session, or a
        # different machine sharing the same UI. Trusting it blindly made
        # _new_run's FK insert raise `FOREIGN KEY constraint failed`. Ensure the
        # row exists (no-op when it already does, preserving title/created_at).
        await conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (id) VALUES (?)", (session_id,)
        )
        await conn.commit()
        return session_id
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


def _derive_title(content: str) -> str:
    """Backend mirror of the frontend ``deriveTitle`` — first ~40 chars of the
    first user message, word-trimmed with an ellipsis. Kept in sync so a title
    derived backend-side (for GET /sessions) matches what the browser shows."""
    trimmed = " ".join(content.split())
    if len(trimmed) <= 40:
        return trimmed
    cut = trimmed[:40]
    last_space = cut.rfind(" ")
    head = cut[:last_space] if last_space > 20 else cut
    return head + "…"


async def _new_run(conn: aiosqlite.Connection, session_id: int) -> int:
    await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'running')",
        (session_id,),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _finalise(
    conn: aiosqlite.Connection,
    run_id: int,
    session_id: int,
    final_content: str,
    status: str,
) -> int:
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'assistant', ?, ?)",
        (session_id, final_content, run_id),
    )
    await conn.execute(
        "UPDATE runs SET finished_at = datetime('now'), status = ? WHERE id = ?",
        (status, run_id),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


FINALIZE_CAP = 2


def _enforce_finalize_cap(
    candidates: list[SearchCandidate],
) -> list[SearchCandidate]:
    """Truncate to at most ``FINALIZE_CAP`` finalize-flagged picks (preserve
    agent's order). Returns a new list of candidates; never mutates input."""
    kept = 0
    out: list[SearchCandidate] = []
    for c in candidates:
        if c.finalize:
            if kept < FINALIZE_CAP:
                out.append(c)
                kept += 1
            else:
                out.append(replace(c, finalize=False))
        else:
            out.append(c)
    return out


async def _mark_already_in_session(
    conn: aiosqlite.Connection,
    session_id: int,
    candidates: list[SearchCandidate],
) -> list[SearchCandidate]:
    """For ``library:<id>`` candidates, set ``already_in_session=True`` AND
    populate ``papers_id`` when a papers row already exists. (search_library
    filters these out for the LLM, but the agent could resurface a library:
    id from history.)

    ``papers_id`` is what lets the frontend SearchResultList derive its
    "Added" badge from the live references slice — arxiv_id matching alone
    is insufficient for PDF-only papers whose paper_content row has
    ``arxiv_id=NULL`` on both sides.
    """
    out: list[SearchCandidate] = []
    for c in candidates:
        if c.paper_id.startswith("library:"):
            try:
                pcid = int(c.paper_id.removeprefix("library:"))
            except ValueError:
                out.append(c)
                continue
            async with conn.execute(
                "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
                (session_id, pcid),
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                out.append(
                    replace(c, already_in_session=True, papers_id=int(row[0])),
                )
                continue
        out.append(c)
    return out


async def _process_search_results(
    ps_item: SearchResultsYield,
    *,
    pipeline: PaperPipeline,
    conn: aiosqlite.Connection,
    session_id: int,
    unpaywall_email: str | None = None,
) -> list[SearchCandidate]:
    """Enforce finalize cap, mark already_in_session, then auto-attach
    ``finalize=True`` picks. Returns the enriched candidate list ready
    for SSE emission."""
    capped = _enforce_finalize_cap(list(ps_item.candidates))
    marked = await _mark_already_in_session(conn, session_id, capped)
    enriched: list[SearchCandidate] = []
    for c in marked:
        if c.finalize and not c.already_in_session:
            try:
                # For arxiv: candidates the SearchCandidate already carries
                # metadata from SS.  Pass it as metadata_override so the
                # pipeline skips the arXiv metadata API round-trip (M2 fix).
                # ss: and library: prefixes are left as None — the dispatcher
                # handles them without a caller-supplied override.
                # UNVERIFIED candidates carry only the Discoverer's hint title
                # (SS missed + arXiv verify was inconclusive, e.g. a transient
                # 429). Passing that as an override would PERSIST the unverified
                # title and skip the arXiv metadata fetch. Leave it None so the
                # pipeline downloads + adopts the AUTHORITATIVE arXiv title.
                md: ArxivMetadata | None = None
                if c.paper_id.startswith("arxiv:") and c.verified:
                    md = ArxivMetadata(
                        title=c.title,
                        abstract=c.abstract or "",
                        authors=list(c.authors),
                        year=c.year,
                    )
                result = await add_paper_to_session_dispatch(
                    c.paper_id,
                    pipeline=pipeline,
                    conn=conn,
                    session_id=session_id,
                    metadata_override=md,
                    unpaywall_email=unpaywall_email,
                )
                enriched.append(
                    replace(c, auto_added=True, papers_id=result.papers_id),
                )
            except NoIngestibleSourceError as exc:
                enriched.append(
                    replace(
                        c,
                        auto_added=False,
                        error="no_ingestible_source",
                        tried_urls=exc.tried_urls,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — defensive, redacted before emit
                enriched.append(
                    replace(c, auto_added=False, error=redact(str(exc))),
                )
        else:
            enriched.append(c)
    return enriched


async def _resolve_history(
    conn: aiosqlite.Connection,
    session_id: int,
    history: list[HistoryEntry],
) -> list[dict[str, str]]:
    """Resolve the conversational history for this turn.

    Normally the client sends the prior turns in ``req.history`` (built from its
    local store). But a freshly FORKED session (SRS v2.30) is created with empty
    local messages and hydrates the copied history asynchronously; if the user
    resends before hydration lands, ``req.history`` is empty even though the DB
    holds the copied turns. So when the client sends NO history but the session
    already has persisted messages, reconstruct it from the DB. MUST be called
    BEFORE the current user message is persisted, so that message is excluded.
    """
    if history:
        return [h.model_dump() for h in history]
    async with conn.execute(
        "SELECT role, content FROM messages "
        "WHERE session_id = ? AND role IN ('user', 'assistant') "
        "ORDER BY id",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"role": str(r[0]), "content": str(r[1])} for r in rows]


async def _record_user_message(
    conn: aiosqlite.Connection, session_id: int, content: str, run_id: int
) -> None:
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'user', ?, ?)",
        (session_id, content, run_id),
    )
    # Promote the still-default title from the first user message so the
    # session is identifiable in GET /sessions across devices. Fires while the
    # title is the seed 'New chat' OR a fork placeholder ('Fork of …'); the
    # rename moves the title off both sentinels so later turns never overwrite it.
    await conn.execute(
        "UPDATE chat_sessions SET title = ? "
        "WHERE id = ? AND (title = 'New chat' OR title LIKE 'Fork of %')",
        (_derive_title(content), session_id),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Subgraph-driving shims
# ---------------------------------------------------------------------------
#
# ``paper_search`` and ``paper_qa_stream`` are exposed as module-level
# async-generator attributes so:
#
#   1. The chat handler drives the Research subgraphs through these names
#      by default — preserving the rubric-required multi-node LangGraph
#      orchestration as the production code path;
#   2. ``test_chat_sse.py`` can still ``monkeypatch.setattr(chat_module,
#      "paper_search", fake)`` and route a hand-crafted generator straight
#      into the SSE translation loop without spinning up a graph.
#
# The shim drives the compiled subgraph via
# ``astream(stream_mode=["custom", "values"])`` and re-emits the custom-
# stream payloads as the same ``ToolStepYield`` / ``SearchResultsYield`` /
# ``FinalOnlyMessage`` (paper_search) or ``str`` / ``FinalOnlyMessage``
# (paper_qa) shapes the SSE translation loop already understands.
# ---------------------------------------------------------------------------


async def paper_search(
    state: AgentState,
    *,
    adapter: Any,
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    pipeline: PaperPipeline,
    mcp_registry: MCPRegistry,
    suggest: bool = False,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Run the paper_search subgraph and yield ToolStepYield /
    SearchResultsYield / FinalOnlyMessage in stream order.

    When ``suggest=True``, the suggest-mode prompt slots are selected so
    the Parser decomposes the request as a topic-recommendation query
    instead of an explicit-paper lookup.
    """
    parse_slot = (
        "paper_search_parse_suggest/v1" if suggest else "paper_search_parse/v1"
    )
    synth_slot = (
        "paper_search_synthesize_suggest/v1" if suggest else "paper_search_synthesize/v1"
    )
    deps = ResearchDeps(
        adapter=adapter,
        tracer=tracer,
        paper_qa_model=model,
        conn=conn,
        pipeline=pipeline,
        mcp_registry=mcp_registry,
        adapter_kwargs=kwargs or None,
        parse_slot=parse_slot,
        synth_slot=synth_slot,
    )
    graph = build_paper_search_subgraph(deps)
    final_text = ""
    candidates_yielded = False
    async for mode, payload in graph.astream(
        state, stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            evt = payload.get("event")
            if evt == "tool_step":
                yield ToolStepYield(record=payload["record"])
            elif evt == "search_results":
                yield SearchResultsYield(candidates=list(payload["candidates"]))
                candidates_yielded = True
        elif mode == "values" and isinstance(payload, dict):
            if "final_response" in payload:
                final_text = payload["final_response"]
    # If the subgraph didn't surface a final_response (unexpected) keep
    # an empty string rather than crashing the SSE pipeline.
    del candidates_yielded  # ordering already correct via astream
    yield FinalOnlyMessage(final_text)


async def paper_qa_stream(
    state: AgentState,
    *,
    adapter: Any,
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Run the paper_qa subgraph and yield token strings / FinalOnlyMessage
    in stream order.

    The chat layer expects:
      * ``str`` items → emit ``token`` SSE events;
      * ``FinalOnlyMessage`` item → emit a single ``final`` SSE event with
        no preceding tokens (sentinel for empty refs / empty corpus).

    The subgraph carries the final text via ``state["final_response"]``;
    we mirror the legacy generator's behaviour by yielding token strings
    as they arrive and yielding a ``FinalOnlyMessage`` ONLY when the
    final_response came from a non-streaming branch (pq_empty / synth
    short-circuit) — those paths emit zero tokens on the custom stream.
    """
    pipeline = kwargs.pop("pipeline", None)
    mcp_registry = kwargs.pop("mcp_registry", None)
    _settings = load_settings()
    deps = ResearchDeps(
        adapter=adapter,
        tracer=tracer,
        paper_qa_model=model,
        conn=conn,
        pipeline=pipeline if pipeline is not None else _NULL_PIPELINE,
        mcp_registry=mcp_registry if mcp_registry is not None else _NULL_REGISTRY,
        adapter_kwargs=kwargs or None,
        paper_qa_subagent_model=_settings.paper_qa_subagent_model,
        paper_qa_max_section_reads=_settings.paper_qa_max_section_reads,
        recall_enabled=_settings.memory_recall_enabled,
    )
    graph = build_paper_qa_subgraph(deps)
    streamed_any = False
    final_text = ""
    async for mode, payload in graph.astream(
        state, stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            evt = payload.get("event")
            if evt == "token":
                streamed_any = True
                yield payload["text"]
            elif evt == "tool_step":
                # Mirror paper_search: forward each agent-step record as it
                # arrives so the outer chat.py loop emits tool_step SSE
                # events progressively (matches FR-02 trace-panel intent).
                # Without this branch the events fall through to the
                # post-stream drain and all surface at once at end-of-turn.
                yield ToolStepYield(record=payload["record"])
        elif mode == "values" and isinstance(payload, dict):
            if "final_response" in payload:
                final_text = payload["final_response"]
    # Sentinel-only paths (no streamed tokens) surface as FinalOnlyMessage
    # so the chat layer emits one final event without preceding tokens.
    if not streamed_any:
        yield FinalOnlyMessage(final_text)


def _build_slide_qa_answerer(
    *, adapter: Any, tracer: Tracer, model: str, conn: aiosqlite.Connection
) -> Any:
    """Return an async callable answering a slide question via the shared
    paper_qa subgraph; returns the full text (FinalOnlyMessage content or the
    joined token stream). Trace steps land in tool_calls (drained end-of-turn).
    """
    async def _answer(state: AgentState) -> str:
        chunks: list[str] = []
        async for item in paper_qa_stream(
            state, adapter=adapter, tracer=tracer, model=model, conn=conn,
        ):
            if isinstance(item, ToolStepYield):
                continue
            if isinstance(item, FinalOnlyMessage):
                return item.content
            chunks.append(item)
        return "".join(chunks)

    return _answer


async def report_stream(
    state: AgentState,
    *,
    adapter: Any,
    tracer: Tracer,
    conn: aiosqlite.Connection,
    settings: Settings,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Run the Report subgraph and yield ToolStepYield / DeckYield /
    FinalOnlyMessage in stream order.

    The chat layer expects:
      * ``ToolStepYield`` items → emit ``tool_step`` SSE events;
      * ``DeckYield`` items → emit ``deck`` SSE events;
      * ``FinalOnlyMessage`` item → emit the ``final`` SSE event.
    """
    deps = ReportDeps(
        adapter=adapter,
        tracer=tracer,
        conn=conn,
        workspace=settings.workspace_dir,
        plan_model=settings.report_plan_model,
        section_model=settings.report_section_model,
        notes_model=settings.report_notes_model,
        resolve_model=settings.report_resolve_model,
        recall_enabled=settings.memory_recall_enabled,
        slide_style_profile_name=settings.slide_style_profile,
        answer_slide_question=_build_slide_qa_answerer(
            adapter=adapter, tracer=tracer,
            model=settings.paper_qa_model, conn=conn,
        ),
    )
    graph = build_report_subgraph(deps)
    final_text = ""
    async for mode, payload in graph.astream(
        state, stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            evt = payload.get("event")
            if evt == "tool_step":
                yield ToolStepYield(record=payload["record"])
            elif evt == "deck":
                yield DeckYield(deck=payload["deck"])
        elif mode == "values" and isinstance(payload, dict):
            if "final_response" in payload:
                final_text = payload["final_response"]
    yield FinalOnlyMessage(final_text)


# Sentinels so the shims can build ResearchDeps without a real
# pipeline when the caller is using only one branch.
# These are never actually invoked because the corresponding subgraph
# node doesn't touch them — paper_qa subgraph never touches
# ``pipeline``.
class _NullPipeline:  # noqa: D101 — local sentinel
    pass


class _NullRegistry:  # noqa: D101 — local sentinel
    pass


_NULL_PIPELINE: Any = _NullPipeline()
_NULL_REGISTRY: Any = _NullRegistry()


async def run_agent(
    handle: RunHandle,
    session_id: int,
    run_id: int,
    req: ChatRequest,
    settings: Settings,
    adapter: LiteLlmAdapter,
    router_mock: str | None,
    chitchat_mock: str | None,
    memory_op_mock: str | None,
    mcp_registry: Any,
) -> None:
    """Execute one chat turn as a backend-owned background task (FR-15).

    The body is the former ``stream_events`` generator with every
    ``yield {"event": E, "data": D}`` replaced by ``handle.emit({...})`` and
    ``request.app.state.mcp_registry`` replaced by the passed ``mcp_registry``.
    The run survives a client disconnect: it always runs to a terminal status
    and persists the assistant message. Only the explicit Stop endpoint (A3)
    cancels it (via ``CancelledError``, re-raised here for that endpoint to own
    the DB cleanup + ``mark_terminal("cancelled")``).
    """
    async with open_db(settings.db_path) as conn:
            sess_evt = SessionEvent(run_id=run_id, session_id=session_id)
            handle.emit({"event": sess_evt.type,
                   "data": sess_evt.model_dump_json(exclude={"type"})})
            # Resolve history BEFORE persisting the current user message so it is
            # not double-counted. Falls back to DB-reconstructed history for a
            # freshly forked/cross-device session whose client store hasn't
            # hydrated yet (SRS v2.30 fork race).
            resolved_history = await _resolve_history(conn, session_id, req.history)
            await _record_user_message(conn, session_id, req.user_message, run_id)
            tracer = Tracer(conn, run_id=run_id, branch="")
            state: AgentState = {
                "run_id": run_id, "branch": "", "session_id": session_id,
                "user_message": req.user_message,
                "history": resolved_history,
                "current_view_page": req.current_view_page,
                "slide_attached": req.slide_attached,
            }
            last_emitted_step = -1
            # Outbound MCP-client headers: any `MCPClient.call_tool` made
            # by the LangGraph subgraphs below (including the loopback
            # `papers.*` server on this same FastAPI app) reads this
            # contextvar and forwards `X-Paperhub-Session-Id` /
            # `X-Paperhub-Run-Id` headers — the loopback's FastMCP
            # middleware requires the session header or it rejects with a
            # 400 (Task v2.5-7).
            headers_token = set_client_headers_context(
                ClientHeadersContext(session_id=session_id, run_id=run_id),
            )
            try:
                router_kwargs: dict[str, Any] = {}
                if router_mock is not None:
                    router_kwargs["mock_response"] = router_mock
                state = await router_node(
                    state, adapter=adapter, tracer=tracer,
                    model=settings.router_model, **router_kwargs,
                )
                for rec in await drain_tool_calls_since(conn, run_id, last_emitted_step):
                    handle.emit({"event": "tool_step",
                           "data": json.dumps({"record": rec}, separators=(',', ':'))})
                    last_emitted_step = rec["step_index"]
                # Slide-aware QA: build the active-slide context ONLY when the
                # composer chip is attached (deterministic). Both the paper_qa
                # branch and the slides action="qa" guard read state.slide_context.
                state = {
                    **state,
                    "slide_context": (
                        await build_slide_context(
                            conn, session_id=session_id,
                            current_view_page=req.current_view_page,
                        )
                        if req.slide_attached
                        else None
                    ),
                }
                decision = state["routing_decision"]
                evt = RoutingDecisionEvent(run_id=run_id, branch="", decision=decision)
                handle.emit({"event": evt.type,
                       "data": evt.model_dump_json(exclude={"type"})})

                intent = decision.intent
                if intent == "chitchat":
                    chunks: list[str] = []
                    chitchat_kwargs: dict[str, Any] = {}
                    if chitchat_mock is not None:
                        chitchat_kwargs["mock_response"] = chitchat_mock
                    async for token in chitchat_stream(
                        state, adapter=adapter, tracer=tracer,
                        model=settings.chitchat_model, **chitchat_kwargs,
                    ):
                        chunks.append(token)
                        token_evt = TokenEvent(run_id=run_id, branch="", text=token)
                        handle.emit({"event": "token",
                               "data": token_evt.model_dump_json(exclude={"type"})})
                    final_content = "".join(chunks)
                elif intent == "clarify":
                    # The router (which sees history) judged the turn
                    # un-resolvable and supplied a clarifying question in
                    # resolved_query. Surface it deliberately — no pipeline,
                    # no degenerate empty-results re-ask. resolved_query is
                    # already captured in the router tracer row + runs table.
                    final_content = decision.resolved_query or CLARIFY_FALLBACK
                    token_evt = TokenEvent(run_id=run_id, branch="", text=final_content)
                    handle.emit({"event": "token",
                           "data": token_evt.model_dump_json(exclude={"type"})})
                elif intent in ("paper_search", "paper_suggest"):
                    pipeline = PaperPipeline(
                        conn,
                        papers_cache_dir=settings.papers_cache_dir,
                    )
                    final_content = ""
                    # paper_search is module-level so monkeypatch can swap.
                    # suggest=True selects the topic-recommendation prompt slots.
                    async for ps_item in paper_search(
                        state, adapter=adapter, tracer=tracer,
                        model=settings.paper_qa_model, conn=conn,
                        pipeline=pipeline, mcp_registry=mcp_registry,
                        suggest=(intent == "paper_suggest"),
                    ):
                        if isinstance(ps_item, ToolStepYield):
                            handle.emit({
                                "event": "tool_step",
                                "data": json.dumps(
                                    {"record": ps_item.record},
                                    separators=(',', ':'),
                                ),
                            })
                            # max(), not assign: set-based emission in
                            # _ps_process can surface indices out of order;
                            # the watermark must be the high-water mark so the
                            # end-of-turn drain never re-emits a streamed row.
                            last_emitted_step = max(
                                last_emitted_step, ps_item.record["step_index"],
                            )
                        elif isinstance(ps_item, SearchResultsYield):
                            enriched = await _process_search_results(
                                ps_item,
                                pipeline=pipeline,
                                conn=conn,
                                session_id=session_id,
                                unpaywall_email=settings.unpaywall_email,
                            )
                            sr_evt = SearchResultsEvent(
                                run_id=run_id,
                                candidates=[
                                    SearchCandidateModel(**asdict(c))
                                    for c in enriched
                                ],
                            )
                            # Persist the cards on the run so they replay
                            # cross-device (GET /sessions/{id}/messages), not
                            # just in the browser that ran the search.
                            await conn.execute(
                                "UPDATE runs SET search_results_json = ? "
                                "WHERE id = ?",
                                (
                                    json.dumps(
                                        [c.model_dump() for c in sr_evt.candidates],
                                        separators=(",", ":"),
                                    ),
                                    run_id,
                                ),
                            )
                            await conn.commit()
                            handle.emit({
                                "event": sr_evt.type,
                                "data": sr_evt.model_dump_json(exclude={"type"}),
                            })
                            # Auto-attach may have produced new tool_calls rows
                            # (e.g. via pipeline.ingest). Drain so the client
                            # sees them in order.
                            for rec in await drain_tool_calls_since(
                                conn, run_id, last_emitted_step,
                            ):
                                handle.emit({
                                    "event": "tool_step",
                                    "data": json.dumps(
                                        {"record": rec},
                                        separators=(',', ':'),
                                    ),
                                })
                                last_emitted_step = rec["step_index"]
                        elif isinstance(ps_item, FinalOnlyMessage):
                            final_content = ps_item.content
                elif intent == "paper_qa":
                    qa_chunks: list[str] = []
                    final_content = ""
                    final_only_seen = False
                    async for item in paper_qa_stream(
                        state, adapter=adapter, tracer=tracer,
                        model=settings.paper_qa_model,
                        conn=conn,
                    ):
                        if isinstance(item, ToolStepYield):
                            # Per-step trace event from the paper_qa subgraph
                            # (resolve / map / synthesize). Forward immediately
                            # so the trace panel surfaces progress in real time
                            # instead of waiting for end-of-turn drain.
                            handle.emit({
                                "event": "tool_step",
                                "data": json.dumps(
                                    {"record": item.record},
                                    separators=(',', ':'),
                                ),
                            })
                            # max(): paper_qa's set-based dispatch drain can
                            # also surface indices out of order (see
                            # _pq_dispatch). High-water mark prevents the
                            # end-of-turn drain from re-emitting streamed rows.
                            last_emitted_step = max(
                                last_emitted_step, item.record["step_index"],
                            )
                        elif isinstance(item, FinalOnlyMessage):
                            # Sentinel path: empty refs / empty corpus.
                            final_content = item.content
                            final_only_seen = True
                        else:
                            qa_chunks.append(item)
                            token_evt = TokenEvent(
                                run_id=run_id, branch="", text=item,
                            )
                            handle.emit({
                                "event": "token",
                                "data": token_evt.model_dump_json(exclude={"type"}),
                            })
                    if not final_only_seen:
                        final_content = "".join(qa_chunks)
                elif intent == "library_stats":
                    registry = mcp_registry
                    # SearchResultsYield → search_results forwarding (E1 Task 2)
                    # auto-attaches finalize picks via the same pipeline the
                    # paper_search branch uses. sql_agent only emits library:<id>
                    # candidates (already in the corpus), so this is normally a
                    # no-op attach, but reusing _process_search_results keeps the
                    # enrichment (already_in_session, papers_id) identical.
                    stats_pipeline = PaperPipeline(
                        conn,
                        papers_cache_dir=settings.papers_cache_dir,
                    )
                    sql_chunks: list[str] = []
                    async for item in sql_agent_stream(
                        state, adapter=adapter, tracer=tracer, registry=registry,
                        planner_model=settings.sql_agent_model,
                        conn=conn,
                        recall_enabled=settings.memory_recall_enabled,
                        emit_tool_steps=True,
                    ):
                        if isinstance(item, ToolStepYield):
                            # Forward each agent step as it commits so the trace
                            # panel fills progressively instead of all-at-end via
                            # the post-stream drain (matches paper_search/qa).
                            handle.emit({"event": "tool_step",
                                   "data": json.dumps({"record": item.record},
                                                      separators=(',', ':'))})
                            last_emitted_step = max(
                                last_emitted_step, item.record["step_index"],
                            )
                            continue
                        if isinstance(item, SearchResultsYield):
                            # Mirror the paper_search branch: enrich → emit a
                            # search_results SSE event → persist on the run so it
                            # replays cross-device. Must NOT append to sql_chunks
                            # (it must never become answer text). Unlike
                            # paper_search we intentionally OMIT the inline
                            # drain_tool_calls_since here: library:<id> attaches
                            # are pure INSERTs (no ingest/LLM → no tool_calls
                            # rows), so the end-of-turn drain suffices.
                            enriched = await _process_search_results(
                                item,
                                pipeline=stats_pipeline,
                                conn=conn,
                                session_id=session_id,
                                unpaywall_email=settings.unpaywall_email,
                            )
                            sr_evt = SearchResultsEvent(
                                run_id=run_id,
                                candidates=[
                                    SearchCandidateModel(**asdict(c))
                                    for c in enriched
                                ],
                            )
                            await conn.execute(
                                "UPDATE runs SET search_results_json = ? "
                                "WHERE id = ?",
                                (
                                    json.dumps(
                                        [c.model_dump() for c in sr_evt.candidates],
                                        separators=(",", ":"),
                                    ),
                                    run_id,
                                ),
                            )
                            await conn.commit()
                            handle.emit({
                                "event": sr_evt.type,
                                "data": sr_evt.model_dump_json(exclude={"type"}),
                            })
                            continue
                        sql_chunks.append(item)
                        token_evt = TokenEvent(run_id=run_id, branch="", text=item)
                        handle.emit({"event": "token",
                               "data": token_evt.model_dump_json(exclude={"type"})})
                    final_content = "".join(sql_chunks)
                elif intent == "memory":
                    registry = mcp_registry
                    memory_kwargs: dict[str, Any] = {}
                    if memory_op_mock is not None:
                        memory_kwargs["op_mock"] = memory_op_mock
                    result_state = await memory_node(
                        state, adapter=adapter, tracer=tracer, registry=registry,
                        model=settings.router_model,
                        **memory_kwargs,
                    )
                    final_content = result_state.get("final_response", "")
                    token_evt = TokenEvent(run_id=run_id, branch="", text=final_content)
                    handle.emit({"event": "token",
                           "data": token_evt.model_dump_json(exclude={"type"})})
                elif intent == "slides":
                    # F4.5 v2.25: deterministic style-command intercepts.
                    #
                    # Two commands short-circuit the LangGraph entirely:
                    #   - "reset slide style" → DELETE slide_style_overrides
                    #   - "remember this style for all future chats" → write/
                    #     replace the slide_style_global memory row
                    # Both emit a plain-text reply and do NOT invoke the slide
                    # pipeline. Creative style mutations ("make it dark serif")
                    # do not match here — they fall through to the slide
                    # agent's replace_preamble tool.
                    style_action = classify_style_command(req.user_message)
                    if style_action is not None:
                        intercept_reply = await handle_style_command(
                            action=style_action,
                            session_id=session_id,
                            conn=conn,
                        )
                        final_content = intercept_reply
                        token_evt = TokenEvent(
                            run_id=run_id, branch="", text=final_content,
                        )
                        handle.emit({
                            "event": "token",
                            "data": token_evt.model_dump_json(exclude={"type"}),
                        })
                        # Don't fall through to the LangGraph; final/drain
                        # below in the shared epilogue still fires.
                    else:
                        final_content = ""
                        # report_stream is module-level so monkeypatch can swap it.
                        async for rs_item in report_stream(
                            state, adapter=adapter, tracer=tracer, conn=conn,
                            settings=settings,
                        ):
                            if isinstance(rs_item, ToolStepYield):
                                handle.emit({
                                    "event": "tool_step",
                                    "data": json.dumps(
                                        {"record": rs_item.record},
                                        separators=(',', ':'),
                                    ),
                                })
                                last_emitted_step = max(
                                    last_emitted_step, rs_item.record["step_index"],
                                )
                            elif isinstance(rs_item, DeckYield):
                                handle.emit({
                                    "event": "deck",
                                    "data": json.dumps(rs_item.deck, separators=(',', ':')),
                                })
                            elif isinstance(rs_item, FinalOnlyMessage):
                                final_content = rs_item.content
                else:
                    final_content = await stub_response(state, intent=intent)

                for rec in await drain_tool_calls_since(conn, run_id, last_emitted_step):
                    handle.emit({"event": "tool_step",
                           "data": json.dumps({"record": rec}, separators=(',', ':'))})
                    last_emitted_step = rec["step_index"]

                message_id = await _finalise(
                    conn, run_id, session_id, final_content, status="ok",
                )
                final_evt = FinalEvent(
                    run_id=run_id, branch="",
                    message_id=message_id, content=final_content,
                )
                handle.emit({"event": final_evt.type,
                       "data": final_evt.model_dump_json(exclude={"type"})})
                handle.final_message_id = message_id
                handle.mark_terminal("ok", now=time.monotonic())
            except asyncio.CancelledError:
                # Explicit Stop (A3) cancels this task. The cancel endpoint owns
                # the DB cleanup (retracting the partial assistant message) AND
                # calls mark_terminal("cancelled", ...). Do NOT _finalise or
                # mark_terminal here — just re-raise so the cancel propagates.
                raise
            except Exception as exc:
                safe_msg = redact(str(exc))
                await _finalise(conn, run_id, session_id, safe_msg, status="error")
                err_evt = ErrorEvent(run_id=run_id, branch="", message=safe_msg)
                handle.emit({"event": err_evt.type,
                       "data": err_evt.model_dump_json(exclude={"type"})})
                handle.mark_terminal("error", now=time.monotonic())
            finally:
                # Reset before the task returns; otherwise the contextvar would
                # leak into the next request that happens to run on the same
                # asyncio task (FastAPI workers pool tasks). Reset is cheap and
                # idempotent. Do NOT push a subscriber sentinel here —
                # mark_terminal already closes subscriber queues on the
                # ok/error paths, and the cancel endpoint does it for cancelled.
                reset_client_headers_context(headers_token)


async def _terminal_events_from_db(
    conn: aiosqlite.Connection,
    run_id: int,
    status: str,
    since: int,
) -> list[dict[str, Any]]:
    """Build synthetic terminal events from persisted DB state.

    Only emits when ``since == 0`` (a client already past cursor 0 needs
    nothing more from the DB fallback).  Returns a list of event dicts with
    ``{"event": <type>, "data": <json string>}`` — the same shape the live
    SSE path emits.
    """
    if since != 0:
        return []

    if status == "cancelled":
        return []

    # Fetch the assistant message row for this run.
    async with conn.execute(
        "SELECT id, content FROM messages "
        "WHERE run_id = ? AND role = 'assistant' "
        "ORDER BY id DESC LIMIT 1",
        (run_id,),
    ) as cur:
        row = await cur.fetchone()

    if status == "ok":
        if row is None:
            return []
        msg_id, content = int(row[0]), str(row[1])
        final_evt = FinalEvent(
            run_id=run_id, branch="", message_id=msg_id, content=content,
        )
        return [
            {
                "event": final_evt.type,
                "data": final_evt.model_dump_json(exclude={"type"}),
            }
        ]

    if status == "error":
        content = str(row[1]) if row is not None else "An error occurred."
        err_evt = ErrorEvent(run_id=run_id, branch="", message=content)
        return [
            {
                "event": err_evt.type,
                "data": err_evt.model_dump_json(exclude={"type"}),
            }
        ]

    # status == "interrupted" (or any unrecognised terminal)
    return [
        {
            "event": "interrupted",
            "data": json.dumps({"run_id": run_id}),
        }
    ]


@router.get("/chat/runs/{run_id}/events")
async def run_events(run_id: int, since: int = 0) -> dict[str, object]:
    handle = broker.get(run_id)
    if handle is not None:
        events, cursor = handle.events_since(since)
        return {"status": handle.status, "events": events, "next_cursor": cursor}
    # Handle absent: evicted after terminal, or lost to a restart → DB fallback.
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT status FROM runs WHERE id = ?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
        status = str(row[0]) if row else "interrupted"
        events = await _terminal_events_from_db(conn, run_id, status, since)
    return {"status": status, "events": events, "next_cursor": since + len(events)}


class CancelRequest(BaseModel):
    run_id: int


@router.post("/chat/cancel")
async def cancel_run(req: CancelRequest) -> dict[str, str]:
    handle = broker.get(req.run_id)
    if handle is not None and handle.task is not None and not handle.task.done():
        handle.task.cancel()
        handle.mark_terminal("cancelled", now=time.monotonic())
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        # guard BOTH writes on still-'running' so a Stop racing completion can't nuke a real answer
        await conn.execute(
            "DELETE FROM messages WHERE run_id = ? AND run_id IN "
            "(SELECT id FROM runs WHERE id = ? AND status = 'running')",
            (req.run_id, req.run_id))
        await conn.execute(
            "UPDATE runs SET finished_at=datetime('now'), status='cancelled' "
            "WHERE id = ? AND status = 'running'", (req.run_id,))
        await conn.commit()
    return {"status": "cancelled", "run_id": str(req.run_id)}


@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request) -> EventSourceResponse:
    settings = load_settings()
    adapter = LiteLlmAdapter()
    router_mock = os.environ.get("PAPERHUB_ROUTER_MOCK")
    chitchat_mock = os.environ.get("PAPERHUB_CHITCHAT_MOCK")
    memory_op_mock = os.environ.get("PAPERHUB_MEMORY_OP_MOCK")
    mcp_registry = request.app.state.mcp_registry

    # Create the run id up front (the broker keys on it), then spawn the agent
    # as a backend-owned background task. A client disconnect detaches the
    # subscriber but never cancels the task (FR-15 / D4).
    async with open_db(settings.db_path) as conn:
        session_id = await _ensure_session(conn, req.session_id)
        run_id = await _new_run(conn, session_id)

    handle = broker.register(run_id)
    handle.task = asyncio.create_task(
        run_agent(
            handle, session_id, run_id, req, settings, adapter,
            router_mock, chitchat_mock, memory_op_mock, mcp_registry,
        ),
    )
    _live_tasks.add(handle.task)
    handle.task.add_done_callback(_live_tasks.discard)

    async def subscriber() -> AsyncIterator[dict[str, Any]]:
        q = handle.subscribe()
        # SNAPSHOT immediately after subscribe with NO await in between: emit()
        # is synchronous, so nothing can interleave in this window. `replay`
        # holds everything emitted before the snapshot; `q` holds everything
        # emitted after — a clean partition with no duplicate and no gap.
        replay = list(handle.events)
        try:
            for past in replay:
                yield past
            while not (handle.done.is_set() and q.empty()):
                evt = await q.get()
                if evt is None:  # terminal sentinel from mark_terminal
                    break
                yield evt
        finally:
            # DISCONNECT = unsubscribe only; the background task keeps running.
            handle.unsubscribe(q)

    return EventSourceResponse(subscriber())
