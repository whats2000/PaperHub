import json
import os
from collections.abc import AsyncIterator
from typing import Any, Literal

import aiosqlite
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from paperhub.agents.chitchat import chitchat_stream
from paperhub.agents.router import router_node
from paperhub.agents.state import AgentState
from paperhub.agents.stubs import stub_response
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.models.events import (
    ErrorEvent,
    FinalEvent,
    RoutingDecisionEvent,
    TokenEvent,
)
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


async def _record_user_message(
    conn: aiosqlite.Connection, session_id: int, content: str, run_id: int
) -> None:
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'user', ?, ?)",
        (session_id, content, run_id),
    )
    await conn.commit()


async def _drain_tool_calls_since(
    conn: aiosqlite.Connection, run_id: int, after_step: int,
) -> list[dict[str, Any]]:
    async with conn.execute(
        "SELECT run_id, branch, step_index, parent_step, agent, tool, model, "
        "args_redacted_json, result_summary_json, latency_ms, token_in, token_out, status, error "
        "FROM tool_calls WHERE run_id = ? AND step_index > ? ORDER BY step_index",
        (run_id, after_step),
    ) as cur:
        rows = await cur.fetchall()
    cols = ("run_id", "branch", "step_index", "parent_step", "agent", "tool", "model",
            "args_redacted_json", "result_summary_json", "latency_ms",
            "token_in", "token_out", "status", "error")
    out: list[dict[str, Any]] = []
    for r in rows:
        d: dict[str, Any] = dict(zip(cols, r, strict=True))
        for key in ("args_redacted_json", "result_summary_json"):
            if d[key]:
                d[key] = json.loads(d[key])
        out.append(d)
    return out


@router.post("/chat")
async def chat_endpoint(req: ChatRequest, _request: Request) -> EventSourceResponse:  # noqa: ARG001
    settings = load_settings()
    adapter = LiteLlmAdapter()
    router_mock = os.environ.get("PAPERHUB_ROUTER_MOCK")
    chitchat_mock = os.environ.get("PAPERHUB_CHITCHAT_MOCK")

    async def stream_events() -> AsyncIterator[dict[str, Any]]:
        async with open_db(settings.db_path) as conn:
            await apply_schema(conn)
            session_id = await _ensure_session(conn, req.session_id)
            run_id = await _new_run(conn, session_id)
            await _record_user_message(conn, session_id, req.user_message, run_id)
            tracer = Tracer(conn, run_id=run_id, branch="")
            state: AgentState = {
                "run_id": run_id, "branch": "", "session_id": session_id,
                "user_message": req.user_message,
                "history": [h.model_dump() for h in req.history],
            }
            last_emitted_step = -1
            try:
                router_kwargs: dict[str, Any] = {}
                if router_mock is not None:
                    router_kwargs["mock_response"] = router_mock
                state = await router_node(
                    state, adapter=adapter, tracer=tracer,
                    model=settings.router_model, **router_kwargs,
                )
                for rec in await _drain_tool_calls_since(conn, run_id, last_emitted_step):
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
                else:
                    final_content = await stub_response(state, intent=intent)

                for rec in await _drain_tool_calls_since(conn, run_id, last_emitted_step):
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
                await _finalise(conn, run_id, session_id, str(exc), status="error")
                err_evt = ErrorEvent(run_id=run_id, branch="", message=str(exc))
                yield {"event": err_evt.type,
                       "data": err_evt.model_dump_json(exclude={"type"})}

    return EventSourceResponse(stream_events())
