"""Outbound protection REST surface (Plan H5a): read-only log + held queue.

There is deliberately no delete/update route for the log. A held request is
decided by the user: send (as shown or edited) or discard. Sending does not
search here: a live chat turn paused on the card gets the decision and sends
it itself (``resumed: true``); with no live turn waiting, the chat's
follow-up turn does, with the held id as its approval.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from paperhub import approvals
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.outbound import destinations, held, log, trusted_search
from paperhub.outbound.checkpoint import find_term
from paperhub.outbound.policy import load_policy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/outbound")


async def _query(
    decision: str | None, destination: str | None, since: str | None, limit: int
) -> list[log.LogRow]:
    try:
        async with open_db(load_settings().db_path) as conn:
            return await log.query(
                conn, decision=decision, destination=destination,
                since=since, limit=limit,
            )
    except log.InvalidSince as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/log")
async def get_log(
    decision: str | None = None,
    destination: str | None = None,
    since: str | None = None,
    limit: int = Query(200, ge=1, le=5000),
) -> list[dict[str, Any]]:
    rows = await _query(decision, destination, since, limit)
    return [asdict(r) for r in rows]


@router.get("/log.csv")
async def get_log_csv(
    decision: str | None = None,
    destination: str | None = None,
    since: str | None = None,
    limit: int = Query(5000, ge=1, le=100000),
) -> Response:
    rows = await _query(decision, destination, since, limit)
    return Response(
        log.to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="outbound-log.csv"'},
    )


@router.get("/destinations")
async def get_destinations() -> list[dict[str, str]]:
    return destinations.list_destinations()


@router.get("/trusted-search/status")
async def get_trusted_search_status() -> dict[str, Any]:
    """Probe the configured trusted search service (short timeout, never 5xx)."""
    return await trusted_search.probe(load_policy().trusted_search_url)


@router.get("/held")
async def get_held() -> list[dict[str, Any]]:
    async with open_db(load_settings().db_path) as conn:
        await held.sweep_stale(conn)
        rows = await held.list_pending(conn)
    return [asdict(r) for r in rows]


@router.get("/held/count")
async def get_held_count() -> dict[str, int]:
    async with open_db(load_settings().db_path) as conn:
        await held.sweep_stale(conn)
        return {"count": await held.count_pending(conn)}


class SendHeldRequest(BaseModel):
    #: The edited query; omitted = send the text as shown.
    text: str | None = None


async def _decidable(conn: aiosqlite.Connection, held_id: str) -> held.HeldRow:
    """The held row, or 404 (unknown) / 409 (already decided, its turn was
    stopped or its chat deleted)."""
    row = await held.get(conn, held_id)
    if row is None:
        raise HTTPException(status_code=404, detail="held request not found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"held request is {row.status}")
    if row.run_id is not None:
        async with conn.execute(
            "SELECT status FROM runs WHERE id = ?", (row.run_id,)) as cur:
            run = await cur.fetchone()
        if run is not None and run[0] == "cancelled":
            raise HTTPException(status_code=409, detail="the chat turn was stopped")
    if row.session_id is not None:
        async with conn.execute(
            "SELECT deleted_at FROM chat_sessions WHERE id = ?",
            (row.session_id,)) as cur:
            sess = await cur.fetchone()
        if sess is None or sess[0] is not None:
            raise HTTPException(status_code=409, detail="the chat was deleted")
    return row


@router.post("/held/{held_id}/send", response_model=None)
async def send_held(held_id: str, body: SendHeldRequest) -> dict[str, Any] | JSONResponse:
    """Approve a held request (every pending row of its card). An edited
    text is re-checked against the sensitive terms only. A live turn paused
    on the card then sends it; otherwise the chat's follow-up turn
    (``approved_held_id``) does."""
    async with open_db(load_settings().db_path) as conn:
        row = await _decidable(conn, held_id)
        text = row.text if body.text is None else body.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="empty_text")
        if text != row.text:
            hit = find_term(text, load_policy().terms)
            if hit is not None:
                return JSONResponse(status_code=422,
                                    content={"detail": "sensitive_term", "term": hit})
        # Unattended (no chat to continue in): its caller resends it, once,
        # after this request has claimed the row.
        fn = held.resender(row.caller) if row.session_id is None else None
        if row.session_id is None and fn is None:
            # Nothing can send it: never record it as sent; it stays pending.
            raise HTTPException(status_code=409, detail="no_resender")
        try:
            ids = await held.decide(conn, row, "sent", actor="user",
                                    sent_text=text, resend=fn)
        except held.ResendFailed as exc:  # the row is pending again
            logger.warning("outbound: resend of %s failed: %s", held_id, exc)
            raise HTTPException(status_code=502, detail="resend_failed") from exc
    if held_id not in ids:  # decided concurrently
        raise HTTPException(status_code=409, detail="held request already decided")
    resumed = _hand_over(ids, approvals.ApprovalDecision(
        True, text, edited=text != row.text))
    return {"status": "sent", "held_id": held_id, "held_ids": ids, "text": text,
            "resumed": resumed}


def _hand_over(ids: list[str], decision: approvals.ApprovalDecision) -> bool:
    """Give the decision to the paused run waiting on these rows; True when
    one was waiting (it acts on it, so no follow-up turn is needed)."""
    delivered = [approvals.deliver(f"held:{i}", decision) for i in ids]
    return any(d is not None for d in delivered)


@router.post("/held/{held_id}/discard")
async def discard_held(held_id: str) -> dict[str, Any]:
    """Discard a held request (every pending row of its card)."""
    async with open_db(load_settings().db_path) as conn:
        row = await _decidable(conn, held_id)
        ids = await held.decide(conn, row, "discarded", actor="user")
    if held_id not in ids:
        raise HTTPException(status_code=409, detail="held request already decided")
    resumed = _hand_over(ids, approvals.ApprovalDecision(False))
    return {"status": "discarded", "held_id": held_id, "held_ids": ids,
            "resumed": resumed}
