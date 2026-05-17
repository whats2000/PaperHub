"""POST /chat — SSE streaming chat endpoint.

Pipeline (per design §8):
1. Insert a ``runs`` row with ``status='running'``.
2. Emit ``routing_decision`` event (Router.classify).
3. If intent == ``paper_qa``:
   a. ResearchAgent.answer → retrieves chunks + generates answer.
   b. Emit one ``tool_step`` event per tool_call row recorded for this run.
   c. Emit one ``token`` event with the full answer text (Phase A; Phase B streams).
   d. Emit ``citation`` events.
4. If intent == ``chitchat``:
   Emit ``final`` with a polite out-of-scope message.
5. Insert assistant ``messages`` row, update ``runs.status='ok'``, emit ``final``.
6. On any exception: emit ``error``, update ``runs.status='failed'``.

HTTP transport: ``sse_starlette.sse.EventSourceResponse`` (sets
``Cache-Control: no-cache`` / ``Connection: keep-alive`` automatically).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from paperhub.agents.research import ResearchAgent
from paperhub.agents.router import BinaryRoutingDecision, Router
from paperhub.agents.state import AgentState
from paperhub.api.sse import (
    CitationEvent,
    ErrorEvent,
    FinalEvent,
    RoutingDecisionEvent,
    TokenEvent,
    ToolStepEvent,
)
from paperhub.config import Settings, get_settings
from paperhub.data.db import connect
from paperhub.data.models import RoutingDecision, ToolCall
from paperhub.data.vectors import ChromaVectorStore
from paperhub.llm.adapter import LiteLlmAdapter, LlmAdapter
from paperhub.llm.prompts import PromptRegistry
from paperhub.rag.embedder import Embedder
from paperhub.rag.retriever import Retriever
from paperhub.tracing.tracer import ToolCallTracer

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    session_id: UUID | None = None


# ---------------------------------------------------------------------------
# Dependency providers — instantiated per-request; override in tests via
# app.dependency_overrides[get_adapter] = lambda: fake_adapter
# ---------------------------------------------------------------------------


def get_prompts() -> PromptRegistry:
    return PromptRegistry.load_default()


def get_adapter(settings: Settings = Depends(get_settings)) -> LlmAdapter:  # noqa: B008
    """Return a production LlmAdapter configured from settings."""
    return LiteLlmAdapter(
        small_model=settings.router_model,
        flagship_model=settings.generation_model,
    )


def get_retriever(settings: Settings = Depends(get_settings)) -> Retriever:  # noqa: B008
    """Return a production Retriever configured from settings."""
    chroma_path = settings.chroma_path or (settings.workspace_root / "chroma")
    store = ChromaVectorStore(chroma_path)
    embedder = Embedder(settings.embedding_model)
    return Retriever(store, embedder)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _insert_run(conn: object, run_id: UUID, session_id: UUID | None, started_at: str) -> None:
    assert isinstance(conn, sqlite3.Connection)
    conn.execute(
        "INSERT INTO runs (id, session_id, routing_decision_json, started_at, status) "
        "VALUES (?, ?, NULL, ?, 'running')",
        (str(run_id), str(session_id) if session_id else None, started_at),
    )


def _update_run_status(
    db_path: object,
    run_id: UUID,
    status: str,
    routing_decision: RoutingDecision | None,
) -> None:
    assert isinstance(db_path, Path)
    rd_json = routing_decision.model_dump_json() if routing_decision is not None else None
    finished_at = datetime.now(UTC).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, routing_decision_json=? WHERE id=?",
            (status, finished_at, rd_json, str(run_id)),
        )


def _insert_message(
    db_path: object,
    session_id: UUID,
    role: str,
    content: str,
    run_id: UUID,
) -> None:
    assert isinstance(db_path, Path)
    msg_id = uuid4()
    ts = datetime.now(UTC).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(msg_id), str(session_id), role, content, str(run_id), ts),
        )


def _load_tool_calls(db_path: object, run_id: UUID) -> list[ToolCall]:
    """Load all tool_calls rows for *run_id* and return as Pydantic models."""
    assert isinstance(db_path, Path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT run_id, step_index, parent_step, agent, tool, model, "
            "       args_redacted_json, result_summary_json, "
            "       latency_ms, token_in, token_out, status, error "
            "FROM tool_calls WHERE run_id=? ORDER BY step_index",
            (str(run_id),),
        ).fetchall()
    result: list[ToolCall] = []
    for row in rows:
        result.append(
            ToolCall(
                run_id=UUID(row[0]),
                step_index=row[1],
                parent_step=row[2],
                agent=row[3],
                tool=row[4],
                model=row[5],
                args_redacted=json.loads(row[6]),
                result_summary=json.loads(row[7]) if row[7] is not None else None,
                latency_ms=row[8],
                token_in=row[9],
                token_out=row[10],
                status=row[11],
                error=row[12],
            )
        )
    return result


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------

_CHITCHAT_REPLY = (
    "I can only answer questions about papers you have indexed in PaperHub. "
    "Please import some papers first, then ask a question about their content."
)


async def _chat_stream(
    request: ChatRequest,
    settings: Settings,
    adapter: LlmAdapter,
    retriever: Retriever,
    prompts: PromptRegistry,
) -> AsyncIterator[str]:
    """Async generator yielding JSON-encoded SSE data strings."""
    run_id = uuid4()
    started_at = datetime.now(UTC).isoformat()

    # Ensure a session exists — create an implicit one if none provided
    session_id: UUID = request.session_id or uuid4()

    routing_decision: RoutingDecision | None = None

    try:
        # --- 1. Insert run + user message rows ---
        with connect(settings.db_path) as conn:
            # Create the session row if needed (implicit session)
            conn.execute(
                "INSERT OR IGNORE INTO chat_sessions (id, project_id, title, created_at) "
                "VALUES (?, NULL, NULL, ?)",
                (str(session_id), started_at),
            )
            _insert_run(conn, run_id, session_id, started_at)

        # --- 2. Route the message ---
        router_obj = Router(adapter, prompts)
        decision: BinaryRoutingDecision = await router_obj.classify(request.message)
        routing_decision = decision

        routing_event = RoutingDecisionEvent(data=decision)
        yield json.dumps(routing_event.model_dump(mode="json"))

        # --- 3. Handle intent ---
        if decision.intent == "paper_qa":
            # Record a "retrieval" tool step manually
            tracer = ToolCallTracer(settings.db_path)

            research_agent = ResearchAgent(adapter, prompts, retriever)

            initial_state: AgentState = {
                "run_id": run_id,
                "user_message": request.message,
            }

            # Record a synthetic "retrieval" step before calling the agent
            t0 = time.monotonic()
            final_state = await research_agent.answer(initial_state)
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Record the research step in tool_calls
            tracer.record(
                run_id=run_id,
                step_index=0,
                parent_step=None,
                agent="research_agent",
                tool="research_qa",
                model=settings.generation_model,
                args={"question": request.message},
                result_summary={
                    "chunks_retrieved": len(final_state.get("retrieved_chunks", [])),
                    "answer_length": len(final_state.get("final_response", "") or ""),
                },
                latency_ms=latency_ms,
                token_in=None,
                token_out=None,
                status="ok",
                error=None,
            )

            # Emit tool_step events for all recorded tool_calls
            tool_calls = _load_tool_calls(settings.db_path, run_id)
            for tc in tool_calls:
                step_event = ToolStepEvent(data=tc)
                yield json.dumps(step_event.model_dump(mode="json"))

            # Emit token event with full answer
            answer = final_state.get("final_response") or ""
            token_event = TokenEvent(data=answer)
            yield json.dumps(token_event.model_dump(mode="json"))

            # Emit citation events from retrieved chunks
            retrieved = final_state.get("retrieved_chunks", [])
            for rc in retrieved:
                citation_event = CitationEvent(
                    chunk_id=rc.chunk.id,
                    section=rc.chunk.section,
                    page=rc.chunk.page,
                )
                yield json.dumps(citation_event.model_dump(mode="json"))

            # Persist assistant message
            if session_id is not None:
                _insert_message(settings.db_path, session_id, "assistant", answer, run_id)

            # Finalize run
            _update_run_status(settings.db_path, run_id, "ok", routing_decision)

            final_event = FinalEvent(run_id=run_id, answer=answer)
            yield json.dumps(final_event.model_dump(mode="json"))

        else:
            # chitchat or any other intent
            _update_run_status(settings.db_path, run_id, "ok", routing_decision)
            final_event = FinalEvent(run_id=run_id, answer=_CHITCHAT_REPLY)
            yield json.dumps(final_event.model_dump(mode="json"))

    except Exception as exc:
        log.exception("Error in /chat stream: %s", exc)
        try:
            _update_run_status(settings.db_path, run_id, "failed", routing_decision)
        except Exception:
            log.exception("Failed to update run status to 'failed'")
        error_event = ErrorEvent(message=str(exc))
        yield json.dumps(error_event.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),  # noqa: B008
    adapter: LlmAdapter = Depends(get_adapter),  # noqa: B008
    retriever: Retriever = Depends(get_retriever),  # noqa: B008
    prompts: PromptRegistry = Depends(get_prompts),  # noqa: B008
) -> EventSourceResponse:
    """Stream a chat response as Server-Sent Events.

    The client should consume ``data: <json>`` SSE frames and dispatch on
    the ``type`` field.  The stream ends after a ``final`` or ``error`` event.
    """

    async def _generator() -> AsyncIterator[dict[str, str]]:
        async for data in _chat_stream(request, settings, adapter, retriever, prompts):
            yield {"data": data}

    return EventSourceResponse(_generator())
