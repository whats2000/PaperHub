"""Confirm / dismiss a pending confirm-tier action (H4 Task 8, H5a).

A live turn pauses on its confirmation card (H5a): the decision goes to
that run, which runs the action itself (its events and trace belong to the
turn) and writes the ``pending_actions`` row; the response carries
``resumed: true`` and, for Confirm, the run's ``{status, result}``. With no
live run waiting (backend restarted mid-pause), Confirm runs the tool
directly here (no LLM, no resumed agent loop) and stores the result.

Ask first's "Allow this search?" card (a ``search_papers`` row, H5a) uses
the same endpoints with a body (Confirm: an edited query, "Allow searches
in this chat"; Dismiss: a note for the agent). Its paused run always does
the search; with no live run the card sends nothing (409 ``turn_ended``).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from paperhub import approvals
from paperhub.agents.core.action_tools import build_default_registry
from paperhub.agents.core.tools import ToolContext, ToolOutcome
from paperhub.api.deps import get_llm
from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.db.migrate import INTERRUPTED_ACTION_ERROR
from paperhub.db.pending_actions import action_states
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.outbound.checkpoint import find_term
from paperhub.outbound.policy import load_policy
from paperhub.tracing.tracer import Tracer

router = APIRouter()

#: The Ask-first search card's tool (H5a): its paused run waits on
#: ``search:<id>`` and the endpoints never run it themselves.
SEARCH_TOOL = "search_papers"
#: "Don't allow"'s optional note for the agent.
NOTE_MAX_CHARS = 1000


class ConfirmActionRequest(BaseModel):
    """Search cards only: the edited query (omitted = as shown) and "Allow
    searches in this chat"."""

    text: str | None = None
    allow_chat: bool = False


class DismissActionRequest(BaseModel):
    """Search cards only: "tell the agent what to do instead"."""

    note: str | None = None


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
    # A confirmed action runs outside an agent turn and reads no chunks for
    # outbound calls, so it needs no begin_turn() private-material list.
    registry = build_default_registry(settings)
    return await registry.execute_confirmed(ctx, action_id, tool, args)


async def _fail_claimed(conn: Any, action_id: str) -> None:
    async with write_transaction(conn):
        await conn.execute(
            "UPDATE pending_actions SET status = 'failed', result_json = ? "
            "WHERE id = ? AND status = 'pending'",
            (json.dumps({"error": INTERRUPTED_ACTION_ERROR}), action_id),
        )


_LIVE_CHAT = ("r.session_id NOT IN "
              "(SELECT id FROM chat_sessions WHERE deleted_at IS NOT NULL)")


@router.get("/chat/paused")
async def paused_runs() -> list[dict[str, Any]]:
    """The cards paused runs wait on (H5a), persisted state: a run is paused
    while it is ``running`` and has a pending held search or a pending
    (unclaimed) action. One row per card row: ``{session_id, run_id, kind,
    id}`` by run; the sidebar counts them per chat."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn, conn.execute(
        "SELECT r.session_id, r.id, 'held', h.id, h.created_at "
        "FROM held_requests h JOIN runs r ON r.id = h.run_id "
        "WHERE r.status = 'running' AND h.status = 'pending' AND h.unattended = 0 "
        f"AND {_LIVE_CHAT} "
        "UNION ALL "
        "SELECT r.session_id, r.id, "
        f"CASE WHEN a.tool = '{SEARCH_TOOL}' THEN 'search' ELSE 'action' END, "
        "a.id, a.created_at "
        "FROM pending_actions a JOIN runs r ON r.id = a.run_id "
        "WHERE r.status = 'running' AND a.status = 'pending' "
        f"AND a.result_json IS NULL AND {_LIVE_CHAT} ORDER BY 2, 4",
    ) as cur:
        rows = await cur.fetchall()
    return [{"session_id": int(r[0]), "run_id": int(r[1]), "kind": str(r[2]),
             "id": str(r[3])} for r in rows]


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


@router.post("/chat/actions/{action_id}/confirm", response_model=None)
async def confirm_action(
    action_id: str, request: Request, body: ConfirmActionRequest | None = None,
) -> dict[str, Any] | JSONResponse:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        run_id, session_id, tool, args = await _load_pending(
            conn, action_id, executing=True)
        if tool == SEARCH_TOOL:
            return await _allow_search(
                conn, action_id, session_id, args, body or ConfirmActionRequest())
        await _claim(conn, action_id, '{"claimed": true}')
        live = approvals.deliver(f"action:{action_id}",
                                 approvals.ApprovalDecision(True))
        if live is not None:
            return await _resumed_outcome(conn, action_id, live)
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
        return {"status": "failed", "error": error, "resumed": False}
    return {"status": "done", "result": result, "resumed": False}


async def _allow_search(
    conn: Any, action_id: str, session_id: int, args: dict[str, Any],
    body: ConfirmActionRequest,
) -> dict[str, Any] | JSONResponse:
    """Allow an Ask-first search (H5a): hand the (possibly edited) query to
    the paused run, which searches in the turn, so this returns at once
    (``resumed: true``) and the run writes the row's final state. An edited
    query is checked against the sensitive terms first, as the held card's
    edit is. A card whose turn is gone (backend restarted) sends nothing:
    its row is closed and the answer is 409 ``turn_ended``."""
    shown = str(args.get("query", ""))
    text = shown if body.text is None else body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="empty_text")
    if text != shown:
        hit = find_term(text, load_policy().terms)
        if hit is not None:
            return JSONResponse(status_code=422,
                                content={"detail": "sensitive_term", "term": hit})
    key = f"search:{action_id}"
    if not approvals.has_waiter(key):
        await _close_search(conn, action_id, "dismissed", {"error": "turn_ended"})
        raise HTTPException(status_code=409, detail="turn_ended")
    edited = text != shown
    decision: dict[str, Any] = {"text": text, "edited": edited,
                                "allow_chat": body.allow_chat}
    await _claim(conn, action_id,
                 json.dumps({"claimed": True, "decision": "allowed", **decision}))
    if body.allow_chat:
        async with write_transaction(conn):
            await conn.execute(
                "UPDATE chat_sessions SET allow_searches = 1 WHERE id = ?",
                (session_id,))
    if approvals.deliver(key, approvals.ApprovalDecision(
            True, text, edited=edited, allow_chat=body.allow_chat)) is None:
        # The run ended between the check and the hand-over: nothing ran.
        await _close_search(conn, action_id, "failed", {"error": "turn_ended"})
        raise HTTPException(status_code=409, detail="turn_ended")
    also = (await _allow_waiting_searches(conn, session_id, action_id)
            if body.allow_chat else [])
    return {"status": "allowed", **decision, "resumed": True, "also_allowed": also}


async def _allow_waiting_searches(
    conn: Any, session_id: int, decided_id: str,
) -> list[dict[str, str]]:
    """"Allow searches in this chat" also settles the chat's other search
    cards still waiting in a live turn (parallel searches), each with its
    shown query: the user just allowed searches here, so asking about them
    would contradict the choice. Held cards (a flagged query) and actions
    are not search permissions and keep waiting. A card another tab claimed
    meanwhile, or whose turn is gone, is skipped."""
    async with conn.execute(
        "SELECT id, args_json FROM pending_actions WHERE session_id = ? "
        "AND tool = 'search_papers' AND status = 'pending' "
        "AND result_json IS NULL AND id != ? ORDER BY created_at, id",
        (session_id, decided_id),
    ) as cur:
        rows = [(str(r[0]), str(r[1])) for r in await cur.fetchall()]
    allowed: list[dict[str, str]] = []
    for row_id, args_json in rows:
        key = f"search:{row_id}"
        if not approvals.has_waiter(key):
            continue
        try:
            shown = str(json.loads(args_json).get("query", ""))
        except (ValueError, AttributeError):
            continue
        marker = {"claimed": True, "decision": "allowed", "text": shown,
                  "edited": False, "allow_chat": True, "by_chat_setting": True}
        try:
            await _claim(conn, row_id, json.dumps(marker))
        except HTTPException:
            continue  # decided elsewhere meanwhile
        if approvals.deliver(key, approvals.ApprovalDecision(
                True, shown, allow_chat=True, by_chat_setting=True)) is None:
            await _close_search(conn, row_id, "failed", {"error": "turn_ended"})
            continue
        allowed.append({"action_id": row_id, "text": shown})
    return allowed


async def _close_search(
    conn: Any, action_id: str, status: str, result: dict[str, Any],
) -> None:
    """Close a search card's row (claimed or not) that no run will finish."""
    async with write_transaction(conn):
        await conn.execute(
            "UPDATE pending_actions SET status = ?, result_json = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, json.dumps(result), action_id))


async def _resumed_outcome(
    conn: Any, action_id: str, live: asyncio.Future[dict[str, Any]],
) -> dict[str, Any]:
    """The paused run's outcome of the confirmed action. The run writes the
    row; a run stopped mid-action leaves it failed, never claimed."""
    try:
        outcome = await asyncio.shield(live)
    except asyncio.CancelledError:
        if not live.cancelled():
            raise  # this request was cancelled; the run still finishes it
        await _fail_claimed(conn, action_id)
        outcome = {"status": "failed", "error": INTERRUPTED_ACTION_ERROR}
    return {**outcome, "resumed": True}


@router.post("/chat/actions/{action_id}/dismiss")
async def dismiss_action(
    action_id: str, body: DismissActionRequest | None = None,
) -> dict[str, Any]:
    """Dismiss a card. A search card ("Don't allow") may carry a note the
    paused run passes to the agent as the user's instruction."""
    note = ((body.note if body else None) or "").strip() or None
    if note is not None and len(note) > NOTE_MAX_CHARS:
        raise HTTPException(status_code=422, detail="note_too_long")
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        _run, _session, tool, _args = await _load_pending(conn, action_id)
        search = tool == SEARCH_TOOL
        result = (json.dumps({"decision": "declined", "note": note})
                  if search else None)
        async with write_transaction(conn):
            cur = await conn.execute(
                "UPDATE pending_actions SET status = 'dismissed', result_json = ? "
                "WHERE id = ? AND status = 'pending' AND result_json IS NULL",
                (result, action_id),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=409, detail="action is not pending")
    live = approvals.deliver(
        f"{'search' if search else 'action'}:{action_id}",
        approvals.ApprovalDecision(False, note=note if search else None))
    return {"status": "dismissed", "resumed": live is not None}
