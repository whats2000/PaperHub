"""Confirm / dismiss a pending confirm-tier action (H4 Task 8).

Clicking a confirmation card runs the tool directly: no LLM, no resumed
agent loop. The result is stored on the ``pending_actions`` row.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from paperhub.agents.core.action_tools import build_default_registry
from paperhub.agents.core.tools import ToolContext, ToolOutcome
from paperhub.api.deps import get_llm
from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.db.migrate import INTERRUPTED_ACTION_ERROR
from paperhub.db.pending_actions import action_states
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer

router = APIRouter()


async def _load_pending(
    conn: Any, action_id: str, *, executing: bool = False,
) -> tuple[int, int, str, dict[str, Any]]:
    async with conn.execute(
        "SELECT a.run_id, a.session_id, a.tool, a.args_json, a.status, "
        "a.result_json, r.status, s.deleted_at FROM pending_actions a "
        "LEFT JOIN runs r ON r.id = a.run_id "
        "LEFT JOIN chat_sessions s ON s.id = a.session_id WHERE a.id = ?",
        (action_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown action")
    if row[4] != "pending" or row[5] is not None:
        raise HTTPException(status_code=409, detail=f"action is {row[4]}")
    # A stopped turn's or a deleted chat's action is never executed (a stale
    # tab); it can still be dismissed.
    if executing and (row[6] == "cancelled" or row[7] is not None):
        raise HTTPException(status_code=409, detail="the turn was stopped")
    return int(row[0]), int(row[1]), str(row[2]), json.loads(row[3])


async def _claim(conn: Any, action_id: str, marker: str) -> None:
    """Claim a pending action exactly once. ``result_json`` doubles as the
    claim marker while the status stays ``pending`` (the CHECK constraint has
    no interim state); the final status + result are written once at the end."""
    async with write_transaction(conn):
        cur = await conn.execute(
            "UPDATE pending_actions SET result_json = ? "
            "WHERE id = ? AND status = 'pending' AND result_json IS NULL",
            (marker, action_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="action is not pending")


async def _execute(
    conn: Any, request: Request, action_id: str,
    run_id: int, session_id: int, tool: str, args: dict[str, Any],
) -> ToolOutcome:
    settings = load_settings()
    async with conn.execute(
        "SELECT p.paper_content_id, c.title FROM papers p "
        "JOIN paper_content c ON c.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1", (session_id,),
    ) as cur:
        enabled = [(int(r[0]), str(r[1])) for r in await cur.fetchall()]

    async def _no_emit(_name: str, _payload: dict[str, Any]) -> None:
        return None

    ctx = ToolContext(
        # append_only: the step takes the run's next free index at write time.
        conn=conn, tracer=Tracer(conn, run_id=run_id, branch="", append_only=True),
        session_id=session_id, run_id=run_id, settings=settings,
        adapter=cast(LiteLlmAdapter, get_llm(request)), emit=_no_emit,
        enabled_papers=enabled, history=[],
        state={"run_id": run_id, "session_id": session_id},
        mcp_registry=getattr(request.app.state, "mcp_registry", None),
    )
    registry = build_default_registry(settings)
    return await registry.execute_confirmed(ctx, action_id, tool, args)


async def _fail_claimed(conn: Any, action_id: str) -> None:
    async with write_transaction(conn):
        await conn.execute(
            "UPDATE pending_actions SET status = 'failed', result_json = ? "
            "WHERE id = ? AND status = 'pending'",
            (json.dumps({"error": INTERRUPTED_ACTION_ERROR}), action_id),
        )


@router.get("/chat/actions/{action_id}")
async def get_action(action_id: str) -> dict[str, Any]:
    """The action's current state ``{status, result?, error?}`` (a card
    re-reads it when Confirm/Dismiss answers 409)."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        state = (await action_states(conn, [action_id])).get(action_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown action")
    return state


@router.post("/chat/actions/{action_id}/confirm")
async def confirm_action(action_id: str, request: Request) -> dict[str, Any]:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        run_id, session_id, tool, args = await _load_pending(
            conn, action_id, executing=True)
        await _claim(conn, action_id, '{"claimed": true}')
        error: str | None
        try:
            outcome = await _execute(
                conn, request, action_id, run_id, session_id, tool, args)
            result: dict[str, Any] = {
                "content": outcome.content, "record": outcome.record}
            error = str(outcome.record["error"]) if outcome.record.get("error") else None
        except Exception as exc:  # noqa: BLE001 - never leave the row claimed
            error = f"{type(exc).__name__}: {exc}"
            result = {"error": error}
        except BaseException:
            # Cancelled (shutdown, client gone): never leave the row claimed.
            await asyncio.shield(_fail_claimed(conn, action_id))
            raise
        async with write_transaction(conn):
            await conn.execute(
                "UPDATE pending_actions SET status = ?, result_json = ? WHERE id = ?",
                ("failed" if error else "done", json.dumps(result), action_id),
            )
    if error:
        return {"status": "failed", "error": error}
    return {"status": "done", "result": result}


@router.post("/chat/actions/{action_id}/dismiss")
async def dismiss_action(action_id: str) -> dict[str, str]:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        await _load_pending(conn, action_id)
        async with write_transaction(conn):
            cur = await conn.execute(
                "UPDATE pending_actions SET status = 'dismissed' "
                "WHERE id = ? AND status = 'pending' AND result_json IS NULL",
                (action_id,),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=409, detail="action is not pending")
    return {"status": "dismissed"}
