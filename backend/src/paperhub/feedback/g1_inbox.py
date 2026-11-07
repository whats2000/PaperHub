"""G1 to-label inbox (Plan H2 Task 8): failed answers -> ``g1-inbox.jsonl``.

Each negative feedback row becomes one line carrying the router step's
*recorded* input (``variables``) and output (``observed``) so the G1 harness
can replay it. ``expect`` is left empty: a human fills it, then
``benchmark.agent.cli import-inbox`` promotes only the labelled lines.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.feedback.models import STAGE_OF_REASON

INBOX_NAME = "g1-inbox.jsonl"
# Process-level lock: the read-existing-ids + append pair must be atomic
# across concurrent requests (the API runs the write in a worker thread).
_INBOX_LOCK = threading.Lock()


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return val if isinstance(val, dict) else {}


def _reasons(raw: str | None) -> list[str]:
    """Defensive parse: a corrupt or non-list ``reasons_json`` -> ``[]``."""
    try:
        val = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [r for r in val if isinstance(r, str)] if isinstance(val, list) else []


class NotANegative(ValueError):
    """Some ids are not counted negatives (the caller 422s)."""

    def __init__(self, ids: list[int]) -> None:
        super().__init__(f"not a counted negative: {ids}")
        self.ids = ids


def _is_counted_negative(
    kind: str, polarity: str, status: str | None, verdict: str | None,
) -> bool:
    """An explicit thumbs-down, or an implicit flag confirmed and final."""
    if polarity != "down":
        return False
    return kind == "explicit" or (status == "final" and verdict == "confirmed")


def case_id(feedback_id: int, run_id: int) -> str:
    return f"fb{feedback_id}-run{run_id}"


async def build_inbox_lines(
    conn: aiosqlite.Connection, feedback_ids: list[int],
) -> list[dict[str, Any]] | None:
    """One inbox line per feedback id (input order, de-duplicated), or ``None``
    if any id does not exist (the caller 404s; nothing is written). Raises
    ``NotANegative`` when any id is not a counted negative (nothing is
    written either).

    Every line carries the ROUTER step's variables/observed regardless of its
    ``stage_hint`` (the importer filters by stage)."""
    lines: list[dict[str, Any]] = []
    rejected: list[int] = []
    for fid in dict.fromkeys(feedback_ids):
        async with conn.execute(
            "SELECT run_id, reasons_json, correction, kind, polarity, status, "
            "verdict FROM feedback WHERE id = ?",
            (fid,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        if not _is_counted_negative(str(row[3]), str(row[4]), row[5], row[6]):
            rejected.append(fid)
            continue
        run_id = int(row[0])
        reasons = _reasons(row[1])
        async with conn.execute(
            "SELECT args_redacted_json, result_summary_json FROM tool_calls "
            "WHERE run_id = ? AND agent = 'router' AND tool = 'classify' "
            "ORDER BY step_index LIMIT 1", (run_id,),
        ) as cur:
            step = await cur.fetchone()
        lines.append({
            "case_id": case_id(fid, run_id),
            "stage_hint": STAGE_OF_REASON.get(reasons[0]) if reasons else None,
            "reasons": reasons,
            "correction": row[2],
            "source_run_id": run_id,
            "variables": _loads(step[0]) if step else {},
            "observed": _loads(step[1]) if step else {},
            "expect": {},
        })
    if rejected:
        raise NotANegative(rejected)
    return lines


def inbox_case_ids(path: Path) -> set[str]:
    """The ``case_id`` of every line already in the inbox file."""
    seen: set[str] = set()
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(str(json.loads(raw)["case_id"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return seen


def append_inbox(path: Path, lines: list[dict[str, Any]]) -> int:
    """Append the lines whose ``case_id`` is not already in the file; return
    how many were added. Idempotent across calls and process restarts."""
    with _INBOX_LOCK:
        seen = inbox_case_ids(path)
        fresh = [ln for ln in lines if ln["case_id"] not in seen]
        if fresh:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                for ln in fresh:
                    fh.write(json.dumps(ln, ensure_ascii=False) + "\n")
        return len(fresh)
