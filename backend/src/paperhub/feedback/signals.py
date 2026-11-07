"""Trace-signal reason pre-selection for the thumbs-down popover (Plan H2).

Deterministic, no LLM call: every signal is read from what the run already
recorded — the router step (``agent='router', tool='classify'``, falling back
to ``runs.routing_decision_json``), the ``research/paper_qa:subagent`` steps'
``chunks_read`` counts, step ``status='error'`` rows, and the run's assistant
``messages`` row (the finalize step records only the answer length, so the
answer text for the citation check comes from ``messages``).
"""
from __future__ import annotations

import json
import re
from typing import Any

import aiosqlite

from paperhub.feedback.citations import cited_chunk_ids
from paperhub.feedback.models import REASONS

# The router v2 prompt's multi-intent tie-break wording. The router has no
# structured "tie" field and the spec forbids adding one.
_PRIORITY_RE = re.compile(r"\bpriority\b|優先", re.IGNORECASE)


def _loads(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _error_reason(agent: str, tool: str) -> str:
    if agent == "router":
        return "wrong_route"
    if tool.startswith("paper_qa:"):
        return "wrong_source"
    return "factual"


async def guess_reasons(conn: aiosqlite.Connection, run_id: int) -> list[str]:
    """Return the pre-selected reasons for ``run_id`` in ``REASONS`` order."""
    async with conn.execute(
        "SELECT routing_decision_json FROM runs WHERE id = ?", (run_id,),
    ) as cur:
        run_row = await cur.fetchone()
    if run_row is None:
        return []

    async with conn.execute(
        "SELECT agent, tool, status, error, result_summary_json "
        "FROM tool_calls WHERE run_id = ? ORDER BY branch, step_index",
        (run_id,),
    ) as cur:
        steps = list(await cur.fetchall())

    found: set[str] = set()
    router: dict[str, Any] | None = None
    subagent_reads: list[int] = []
    for agent, tool, status, error, result_raw in steps:
        result = _loads(result_raw)
        if agent == "router" and tool == "classify" and result is not None:
            router = result
        if agent == "research" and tool == "paper_qa:subagent" and status == "ok":
            reads = (result or {}).get("chunks_read")
            subagent_reads.append(reads if isinstance(reads, int) else 0)
        # A user-cancelled step is not a system failure.
        if status == "error" and error != "cancelled":
            found.add(_error_reason(str(agent), str(tool)))
    if router is None:
        router = _loads(run_row[0])

    intent = (router or {}).get("intent")
    reasoning = (router or {}).get("reasoning")
    if intent == "clarify" or (
        isinstance(reasoning, str) and _PRIORITY_RE.search(reasoning)
    ):
        found.add("wrong_route")

    if subagent_reads and all(n == 0 for n in subagent_reads):
        found.add("wrong_source")

    if intent == "paper_qa":
        async with conn.execute(
            "SELECT content FROM messages WHERE run_id = ? AND role = 'assistant' "
            "ORDER BY id DESC LIMIT 1",
            (run_id,),
        ) as cur:
            msg = await cur.fetchone()
        if msg is not None and not cited_chunk_ids(str(msg[0])):
            found.add("citation")

    return [r for r in REASONS if r in found]
