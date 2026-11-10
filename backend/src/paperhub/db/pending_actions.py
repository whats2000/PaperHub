"""Read the state of confirm-tier actions (``pending_actions``, H4).

Shared by the message replay (each confirm card shows its action's current
state) and ``GET /chat/actions/{id}`` (a card re-reads it after a 409).
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import aiosqlite


def action_state(status: str, raw_result: str | None) -> dict[str, Any]:
    """``{status, result?, error?}`` for one row. A pending row whose
    ``result_json`` holds the claim marker is ``running``."""
    try:
        result = json.loads(raw_result) if raw_result else None
    except (json.JSONDecodeError, TypeError, ValueError):
        result = None
    if status == "pending":
        return {"status": "running" if raw_result else "pending"}
    if status == "done":
        return {"status": "done", "result": result}
    if status == "failed":
        error = result.get("error") if isinstance(result, dict) else None
        return {"status": "failed", "error": str(error or "failed")}
    return {"status": status}


async def action_states(
    conn: aiosqlite.Connection,
    action_ids: Iterable[str],
    *,
    session_id: int | None = None,
) -> dict[str, dict[str, Any]]:
    """States of the given actions in one query, keyed by action id. With
    ``session_id``, actions of other sessions are left out (a fork's copied
    card must not act on its parent's action)."""
    ids = sorted({str(a) for a in action_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    sql = f"SELECT id, status, result_json FROM pending_actions WHERE id IN ({marks})"
    args: list[Any] = list(ids)
    if session_id is not None:
        sql += " AND session_id = ?"
        args.append(session_id)
    async with conn.execute(sql, args) as cur:
        rows = await cur.fetchall()
    return {str(r[0]): action_state(str(r[1]), r[2]) for r in rows}
