"""Shared helper for draining tool_calls rows from the DB."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

_COLS = (
    "run_id", "branch", "step_index", "parent_step", "agent", "tool", "model",
    "args_redacted_json", "result_summary_json", "latency_ms",
    "token_in", "token_out", "status", "error", "tool_call_id",
)


async def drain_tool_calls_since(
    conn: aiosqlite.Connection,
    run_id: int,
    after_step: int,
) -> list[dict[str, Any]]:
    """Return tool_calls rows for *run_id* with step_index > *after_step*.

    JSON fields (``args_redacted_json``, ``result_summary_json``) are parsed
    back into dicts so callers get plain Python objects rather than raw strings.
    """
    async with conn.execute(
        "SELECT run_id, branch, step_index, parent_step, agent, tool, model, "
        "args_redacted_json, result_summary_json, latency_ms, token_in, token_out, "
        "status, error, tool_call_id "
        "FROM tool_calls WHERE run_id = ? AND step_index > ? ORDER BY step_index",
        (run_id, after_step),
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d: dict[str, Any] = dict(zip(_COLS, r, strict=True))
        for key in ("args_redacted_json", "result_summary_json"):
            if d[key]:
                d[key] = json.loads(d[key])
        out.append(d)
    return out
