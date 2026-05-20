import json
import os
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from typing import Any, Literal

import aiosqlite
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from paperhub.agents.chitchat import chitchat_stream
from paperhub.agents.graph import CLARIFY_FALLBACK
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
from paperhub.agents.state import AgentState
from paperhub.agents.stubs import stub_response
from paperhub.api.deps import get_chroma
from paperhub.config import load_settings
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
from paperhub.rag.retriever import Retriever
from paperhub.tracing.redactor import redact
from paperhub.tracing.tracer import Tracer

router = APIRouter()


class HistoryEntry(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    session_id: int | None = None
    user_message: str
    history: list[HistoryEntry] = Field(default_factory=list)


async def _ensure_session(conn: aiosqlite.Connection, session_id: int | None) -> int:
    if session_id is not None:
        return session_id
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


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
                md: ArxivMetadata | None = None
                if c.paper_id.startswith("arxiv:"):
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
                )
                enriched.append(
                    replace(c, auto_added=True, papers_id=result.papers_id),
                )
            except NoIngestibleSourceError:
                enriched.append(
                    replace(c, auto_added=False, error="no_ingestible_source"),
                )
            except Exception as exc:  # noqa: BLE001 — defensive, redacted before emit
                enriched.append(
                    replace(c, auto_added=False, error=redact(str(exc))),
                )
        else:
            enriched.append(c)
    return enriched


async def _record_user_message(
    conn: aiosqlite.Connection, session_id: int, content: str, run_id: int
) -> None:
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'user', ?, ?)",
        (session_id, content, run_id),
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
    retriever = kwargs.pop("retriever", None)
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
        retriever=retriever if retriever is not None else _NULL_RETRIEVER,
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
    retriever: Retriever,
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
        retriever=retriever,
        mcp_registry=mcp_registry if mcp_registry is not None else _NULL_REGISTRY,
        adapter_kwargs=kwargs or None,
        paper_qa_subagent_model=_settings.paper_qa_subagent_model,
        paper_qa_max_section_reads=_settings.paper_qa_max_section_reads,
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


# Sentinels so the shims can build ResearchDeps without a real
# pipeline / retriever when the caller is using only one branch.
# These are never actually invoked because the corresponding subgraph
# node doesn't touch them — paper_search subgraph never touches
# ``retriever``; paper_qa subgraph never touches ``pipeline``.
class _NullPipeline:  # noqa: D101 — local sentinel
    pass


class _NullRetriever:  # noqa: D101 — local sentinel
    pass


class _NullRegistry:  # noqa: D101 — local sentinel
    pass


_NULL_PIPELINE: Any = _NullPipeline()
_NULL_RETRIEVER: Any = _NullRetriever()
_NULL_REGISTRY: Any = _NullRegistry()


@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request) -> EventSourceResponse:
    settings = load_settings()
    adapter = LiteLlmAdapter()
    router_mock = os.environ.get("PAPERHUB_ROUTER_MOCK")
    chitchat_mock = os.environ.get("PAPERHUB_CHITCHAT_MOCK")

    async def stream_events() -> AsyncIterator[dict[str, Any]]:
        async with open_db(settings.db_path) as conn:
            session_id = await _ensure_session(conn, req.session_id)
            run_id = await _new_run(conn, session_id)
            sess_evt = SessionEvent(run_id=run_id, session_id=session_id)
            yield {"event": sess_evt.type,
                   "data": sess_evt.model_dump_json(exclude={"type"})}
            await _record_user_message(conn, session_id, req.user_message, run_id)
            tracer = Tracer(conn, run_id=run_id, branch="")
            state: AgentState = {
                "run_id": run_id, "branch": "", "session_id": session_id,
                "user_message": req.user_message,
                "history": [h.model_dump() for h in req.history],
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
                    yield {"event": "tool_step",
                           "data": json.dumps({"record": rec}, separators=(',', ':'))}
                    last_emitted_step = rec["step_index"]
                decision = state["routing_decision"]
                evt = RoutingDecisionEvent(run_id=run_id, branch="", decision=decision)
                yield {"event": evt.type,
                       "data": evt.model_dump_json(exclude={"type"})}

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
                        yield {"event": "token",
                               "data": token_evt.model_dump_json(exclude={"type"})}
                    final_content = "".join(chunks)
                elif intent == "clarify":
                    # The router (which sees history) judged the turn
                    # un-resolvable and supplied a clarifying question in
                    # resolved_query. Surface it deliberately — no pipeline,
                    # no degenerate empty-results re-ask. resolved_query is
                    # already captured in the router tracer row + runs table.
                    final_content = decision.resolved_query or CLARIFY_FALLBACK
                    token_evt = TokenEvent(run_id=run_id, branch="", text=final_content)
                    yield {"event": "token",
                           "data": token_evt.model_dump_json(exclude={"type"})}
                elif intent in ("paper_search", "paper_suggest"):
                    pipeline = PaperPipeline(
                        conn,
                        papers_cache_dir=settings.papers_cache_dir,
                        chroma=get_chroma(request, settings),
                    )
                    mcp_registry: MCPRegistry = request.app.state.mcp_registry
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
                            yield {
                                "event": "tool_step",
                                "data": json.dumps(
                                    {"record": ps_item.record},
                                    separators=(',', ':'),
                                ),
                            }
                            last_emitted_step = ps_item.record["step_index"]
                        elif isinstance(ps_item, SearchResultsYield):
                            enriched = await _process_search_results(
                                ps_item,
                                pipeline=pipeline,
                                conn=conn,
                                session_id=session_id,
                            )
                            sr_evt = SearchResultsEvent(
                                run_id=run_id,
                                candidates=[
                                    SearchCandidateModel(**asdict(c))
                                    for c in enriched
                                ],
                            )
                            yield {
                                "event": sr_evt.type,
                                "data": sr_evt.model_dump_json(exclude={"type"}),
                            }
                            # Auto-attach may have produced new tool_calls rows
                            # (e.g. via pipeline.ingest). Drain so the client
                            # sees them in order.
                            for rec in await drain_tool_calls_since(
                                conn, run_id, last_emitted_step,
                            ):
                                yield {
                                    "event": "tool_step",
                                    "data": json.dumps(
                                        {"record": rec},
                                        separators=(',', ':'),
                                    ),
                                }
                                last_emitted_step = rec["step_index"]
                        elif isinstance(ps_item, FinalOnlyMessage):
                            final_content = ps_item.content
                elif intent == "paper_qa":
                    retriever = Retriever(chroma=get_chroma(request, settings))
                    qa_chunks: list[str] = []
                    final_content = ""
                    final_only_seen = False
                    async for item in paper_qa_stream(
                        state, adapter=adapter, tracer=tracer,
                        model=settings.paper_qa_model, retriever=retriever,
                        conn=conn,
                    ):
                        if isinstance(item, ToolStepYield):
                            # Per-step trace event from the paper_qa subgraph
                            # (resolve / map / synthesize). Forward immediately
                            # so the trace panel surfaces progress in real time
                            # instead of waiting for end-of-turn drain.
                            yield {
                                "event": "tool_step",
                                "data": json.dumps(
                                    {"record": item.record},
                                    separators=(',', ':'),
                                ),
                            }
                            last_emitted_step = item.record["step_index"]
                        elif isinstance(item, FinalOnlyMessage):
                            # Sentinel path: empty refs / empty corpus.
                            final_content = item.content
                            final_only_seen = True
                        else:
                            qa_chunks.append(item)
                            token_evt = TokenEvent(
                                run_id=run_id, branch="", text=item,
                            )
                            yield {
                                "event": "token",
                                "data": token_evt.model_dump_json(exclude={"type"}),
                            }
                    if not final_only_seen:
                        final_content = "".join(qa_chunks)
                else:
                    final_content = await stub_response(state, intent=intent)

                for rec in await drain_tool_calls_since(conn, run_id, last_emitted_step):
                    yield {"event": "tool_step",
                           "data": json.dumps({"record": rec}, separators=(',', ':'))}
                    last_emitted_step = rec["step_index"]

                message_id = await _finalise(
                    conn, run_id, session_id, final_content, status="ok",
                )
                final_evt = FinalEvent(
                    run_id=run_id, branch="",
                    message_id=message_id, content=final_content,
                )
                yield {"event": final_evt.type,
                       "data": final_evt.model_dump_json(exclude={"type"})}
            except Exception as exc:
                safe_msg = redact(str(exc))
                await _finalise(conn, run_id, session_id, safe_msg, status="error")
                err_evt = ErrorEvent(run_id=run_id, branch="", message=safe_msg)
                yield {"event": err_evt.type,
                       "data": err_evt.model_dump_json(exclude={"type"})}
            finally:
                # Reset before the async-generator returns; otherwise the
                # contextvar would leak into the next request that happens
                # to run on the same asyncio task (FastAPI workers pool
                # tasks). Reset is cheap and idempotent.
                reset_client_headers_context(headers_token)

    return EventSourceResponse(stream_events())
