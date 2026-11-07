"""Explicit (thumbs) feedback persistence (Plan H2).

One explicit row per run, enforced by the partial unique index
``idx_feedback_explicit_run``; implicit (reviewer) rows are never touched here.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

import aiosqlite

from paperhub.db.connection import write_transaction
from paperhub.feedback.forks import copied_runs
from paperhub.feedback.models import (
    REASONS,
    STAGE_OF_REASON,
    STAGES,
    FeedbackCase,
    FeedbackSummary,
    Kpis,
    OtherReason,
    Polarity,
    Preset,
    QueueAnswer,
    QueueItem,
    QueueTurn,
    RunFeedback,
    Stage,
    StageSummary,
    Unattributed,
)
from paperhub.feedback.signals import guess_reasons


async def run_exists(conn: aiosqlite.Connection, run_id: int) -> bool:
    async with conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)) as cur:
        return await cur.fetchone() is not None


async def rating_problem(conn: aiosqlite.Connection, run_id: int) -> tuple[int, str] | None:
    """Why ``run_id`` cannot be rated, as ``(http status, detail)``; ``None``
    when it is a finished chat answer with an assistant message. An errored
    chat run still counts: its stored error reply is what the user saw."""
    async with conn.execute(
        "SELECT status, routing_decision_json FROM runs WHERE id = ?", (run_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return 404, f"run {run_id} not found"
    status, routing_raw = row
    if status not in ("ok", "error"):
        return 409, f"run {run_id} has not finished ({status})"
    try:
        routing = json.loads(routing_raw) if routing_raw else {}
    except ValueError:
        routing = {}
    if not isinstance(routing, dict):
        routing = {}
    if routing.get("intent") == "feedback_review" or "ideation_mode" in routing:
        return 422, f"run {run_id} is not a chat answer"
    async with conn.execute(
        "SELECT 1 FROM messages WHERE run_id = ? AND role = 'assistant' LIMIT 1",
        (run_id,),
    ) as cur:
        if await cur.fetchone() is None:
            return 422, f"run {run_id} has no assistant answer"
    return None


async def allowed_reasons(conn: aiosqlite.Connection) -> set[str]:
    """Built-in reasons + promoted preset labels + ``"other"``."""
    async with conn.execute("SELECT label FROM feedback_presets") as cur:
        presets = {str(r[0]) for r in await cur.fetchall()}
    return {*REASONS, *presets, "other"}


_SUPERSEDE_SET = (
    "UPDATE feedback SET status = 'final', verdict = 'rejected', "
    "reasons_json = '[]', updated_at = datetime('now') "
)
_SUPERSEDED = (
    "WHERE kind = 'implicit' AND status = 'needs_confirmation' AND EXISTS "
    "(SELECT 1 FROM feedback e WHERE e.run_id = feedback.run_id "
    "AND e.kind = 'explicit')"
)


async def resolve_superseded(conn: aiosqlite.Connection, dry_run: bool = False) -> int:
    """Close every pending reviewer item whose run the user rated explicitly:
    the thumbs is the user's answer, so the item leaves the queue as
    ``final`` + ``rejected`` (never ``confirmed``, which would count as a
    negative). Idempotent; returns the number of rows closed (or that would
    be, with ``dry_run``)."""
    if dry_run:
        async with conn.execute(
            "SELECT COUNT(*) FROM feedback " + _SUPERSEDED,
        ) as cur:
            return int((await cur.fetchone() or (0,))[0])
    async with write_transaction(conn):
        cur2 = await conn.execute(_SUPERSEDE_SET + _SUPERSEDED)
    return int(cur2.rowcount)


async def upsert_explicit(
    conn: aiosqlite.Connection,
    run_id: int,
    polarity: Polarity | None,
    reasons: list[str],
    other_text: str | None,
    correction: str | None,
) -> None:
    """Create/overwrite the run's explicit feedback; ``None`` deletes it.

    A thumbs-up clears the reasons and the "other" text (they describe a
    failure). Reasons are de-duplicated, order preserved.
    """
    async with write_transaction(conn):
        if polarity is None:
            await conn.execute(
                "DELETE FROM feedback WHERE run_id = ? AND kind = 'explicit'",
                (run_id,),
            )
            return
        # The thumbs answers any pending reviewer question about this run.
        await conn.execute(
            _SUPERSEDE_SET + "WHERE run_id = ? AND kind = 'implicit' "
            "AND status = 'needs_confirmation'", (run_id,),
        )
        if polarity == "up":
            reasons, other_text = [], None
        reasons = list(dict.fromkeys(reasons))
        async with conn.execute(
            "SELECT id FROM messages WHERE run_id = ? AND role = 'assistant' "
            "ORDER BY id DESC LIMIT 1",
            (run_id,),
        ) as cur:
            msg = await cur.fetchone()
        await conn.execute(
            "INSERT INTO feedback (run_id, message_id, kind, polarity, "
            "reasons_json, other_text, correction) "
            "VALUES (?, ?, 'explicit', ?, ?, ?, ?) "
            "ON CONFLICT(run_id) WHERE kind = 'explicit' DO UPDATE SET "
            "message_id = excluded.message_id, polarity = excluded.polarity, "
            "reasons_json = excluded.reasons_json, "
            "other_text = excluded.other_text, correction = excluded.correction, "
            "updated_at = datetime('now')",
            (run_id, msg[0] if msg is not None else None, polarity,
             json.dumps(reasons, ensure_ascii=False), other_text, correction),
        )


async def get_explicit(conn: aiosqlite.Connection, run_id: int) -> RunFeedback | None:
    async with conn.execute(
        "SELECT run_id, polarity, reasons_json, other_text, correction FROM feedback "
        "WHERE run_id = ? AND kind = 'explicit'",
        (run_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return RunFeedback(
        run_id=row[0], polarity=row[1], reasons=json.loads(row[2]),
        other_text=row[3], correction=row[4],
    )


async def list_explicit_for_session(
    conn: aiosqlite.Connection, session_id: int,
) -> list[RunFeedback]:
    async with conn.execute(
        "SELECT f.run_id, f.polarity, f.reasons_json, f.other_text, f.correction "
        "FROM feedback f "
        "JOIN runs r ON r.id = f.run_id "
        "WHERE r.session_id = ? AND f.kind = 'explicit' ORDER BY f.run_id",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        RunFeedback(
            run_id=r[0], polarity=r[1], reasons=json.loads(r[2]),
            other_text=r[3], correction=r[4],
        )
        for r in rows
    ]


async def _turns(
    conn: aiosqlite.Connection, session_id: int,
) -> list[tuple[int, str, str, int | None]]:
    async with conn.execute(
        "SELECT id, role, content, run_id FROM messages "
        "WHERE session_id = ? ORDER BY id", (session_id,),
    ) as cur:
        return [(int(r[0]), str(r[1]), str(r[2]), r[3]) for r in await cur.fetchall()]


def _reply_after(
    turns: list[tuple[int, str, str, int | None]], user_idx: int,
) -> str | None:
    for _id, role, content, _run in turns[user_idx + 1:]:
        if role == "assistant":
            return content
        if role == "user":
            return None
    return None


# A pending implicit row whose run the user already rated with a thumbs is not
# asked again: the explicit feedback is the user's answer.
_NOT_RATED = (
    "NOT EXISTS (SELECT 1 FROM feedback e WHERE e.run_id = f.run_id "
    "AND e.kind = 'explicit')"
)


async def list_queue(conn: aiosqlite.Connection) -> list[QueueItem]:
    """Implicit feedback awaiting the user's confirmation, with full context.
    Runs the user already rated explicitly are left out."""
    async with conn.execute(
        "SELECT f.id, f.run_id, r.session_id, s.title, f.trigger, "
        "f.reasons_json, f.reviewer_rationale "
        "FROM feedback f JOIN runs r ON r.id = f.run_id "
        "JOIN chat_sessions s ON s.id = r.session_id "
        "WHERE f.kind = 'implicit' AND f.status = 'needs_confirmation' "
        "AND s.deleted_at IS NULL AND " + _NOT_RATED + " ORDER BY f.id",
    ) as cur:
        rows = await cur.fetchall()
    items: list[QueueItem] = []
    for fid, run_id, sid, title, trigger, reasons_json, rationale in rows:
        turns = await _turns(conn, sid)
        a_idx = next(
            (i for i, t in enumerate(turns) if t[1] == "assistant" and t[3] == run_id),
            None,
        )
        if a_idx is None:
            continue
        q_idx = next(
            (i for i, t in enumerate(turns) if t[1] == "user" and t[3] == run_id),
            None,
        )
        if q_idx is None:  # fall back to the user turn right before the answer
            q_idx = next(
                (i for i in range(a_idx - 1, -1, -1) if turns[i][1] == "user"), None,
            )
        if q_idx is None:
            continue
        flagged, next_reply = "", None
        if trigger == "fork":
            # Several forks may branch from the same run; the FIRST fork (lowest
            # id) that already has a user message wins. Soft-deleted forks are
            # skipped.
            async with conn.execute(
                "SELECT id FROM chat_sessions WHERE forked_from_run_id = ? "
                "AND deleted_at IS NULL ORDER BY id", (run_id,),
            ) as cur:
                forks = [int(r[0]) for r in await cur.fetchall()]
            for fork_sid in forks:
                # The fork copies every message before the forked one.
                fturns = (await _turns(conn, fork_sid))[q_idx:]
                u = next((i for i, t in enumerate(fturns) if t[1] == "user"), None)
                if u is not None:
                    flagged, next_reply = fturns[u][2], _reply_after(fturns, u)
                    break
        else:
            u = next(
                (i for i in range(a_idx + 1, len(turns)) if turns[i][1] == "user"),
                None,
            )
            if u is not None:
                flagged, next_reply = turns[u][2], _reply_after(turns, u)
        async with conn.execute(
            "SELECT pc.title FROM papers p "
            "JOIN paper_content pc ON pc.id = p.paper_content_id "
            "WHERE p.session_id = ? AND p.enabled = 1 ORDER BY p.id", (sid,),
        ) as cur:
            papers = [str(r[0]) for r in await cur.fetchall()]
        # The reviewer's own reasons win; the trace-signal guess is the fallback.
        guessed = json.loads(reasons_json or "[]") or await guess_reasons(conn, run_id)
        items.append(QueueItem(
            feedback_id=fid, run_id=run_id, session_id=sid, session_title=title,
            papers=papers,
            earlier=[
                QueueTurn(role=t[1], content=t[2])
                for t in turns[:q_idx] if t[1] in ("user", "assistant")
            ],
            question=turns[q_idx][2], answer=turns[a_idx][2],
            flagged=flagged, next_reply=next_reply,
            reviewer_doubt=rationale or "", guessed_reasons=guessed,
        ))
    return items


async def submit_queue(
    conn: aiosqlite.Connection, answers: list[QueueAnswer],
) -> tuple[int, int] | None:
    """Finalise queue items atomically. ``None`` if any id is not in the queue."""
    async with write_transaction(conn):
        for a in answers:
            async with conn.execute(
                "SELECT 1 FROM feedback WHERE id = ? AND kind = 'implicit' "
                "AND status = 'needs_confirmation'", (a.feedback_id,),
            ) as cur:
                if await cur.fetchone() is None:
                    return None
        confirmed = rejected = 0
        for a in answers:
            if a.confirmed:
                confirmed += 1
                verdict, reasons = "confirmed", list(dict.fromkeys(a.reasons))
            else:
                rejected += 1
                verdict, reasons = "rejected", []
            await conn.execute(
                "UPDATE feedback SET status = 'final', verdict = ?, "
                "reasons_json = ?, updated_at = datetime('now') WHERE id = ?",
                (verdict, json.dumps(reasons, ensure_ascii=False), a.feedback_id),
            )
    return confirmed, rejected


# ---------------------------------------------------------------- analysis

# Counted feedback = every explicit row + implicit rows the reviewer/user
# confirmed (status 'final', verdict 'confirmed'). Rejected, uncertain and
# still-pending implicit rows never count. Soft-deleted sessions are excluded.
_COUNTED = (
    "s.deleted_at IS NULL AND (f.kind = 'explicit' OR "
    "(f.kind = 'implicit' AND f.status = 'final' AND f.verdict = 'confirmed'))"
)


async def _preset_stages(conn: aiosqlite.Connection) -> dict[str, str]:
    async with conn.execute("SELECT label, stage FROM feedback_presets") as cur:
        return {str(r[0]): str(r[1]) for r in await cur.fetchall()}


def _row_stages(reasons: list[str], presets: dict[str, str]) -> list[str]:
    """The distinct stages a row's reasons map to, each counted ONCE per row
    (``factual`` + ``off_topic`` is one answer-stage failure, not two).
    ``other`` and unknown reasons map to no stage."""
    out: list[str] = []
    for r in reasons:
        st = STAGE_OF_REASON.get(r) or presets.get(r)
        if st in STAGES and st not in out:
            out.append(st)
    return out


async def _without_fork_copies(
    conn: aiosqlite.Connection, rows: list[Any], run_col: int, kind_col: int,
) -> list[Any]:
    """Keep one run per ``(origin answer, kind)`` among ``rows``: fork copies
    (through forks of forks) are the same answer as their original, so count
    it once. The original's rows win when present, else the earliest copy's —
    so two rated copies of an unrated (or deleted) original count once."""
    copies = await copied_runs(conn)
    keep: dict[tuple[int, str], int] = {}
    for rid, kind in sorted({(int(r[run_col]), str(r[kind_col])) for r in rows}):
        key = (copies.get(rid, rid), kind)
        if rid == key[0] or key not in keep:
            keep[key] = rid
    kept = set(keep.items())
    return [
        r for r in rows
        if ((copies.get(int(r[run_col]), int(r[run_col])), str(r[kind_col])),
            int(r[run_col])) in kept
    ]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def build_summary(
    conn: aiosqlite.Connection, now: datetime | None = None,
) -> FeedbackSummary:
    """Aggregate counted feedback for the Feedback page.

    Negatives = explicit ``down`` + confirmed implicit. Approval = explicit up
    / explicit total (``None`` when no explicit feedback). ``weekly`` is the
    last 4 ISO weeks (Monday start, UTC) oldest first, so ``weekly[3]`` is the
    current week and ``delta = weekly[3] - weekly[2]``. Fork copies of one
    answer count once per kind (``_without_fork_copies``).
    """
    today = (now or datetime.now(UTC)).date()
    this_monday = _monday(today)
    week_starts = [this_monday - timedelta(weeks=3 - i) for i in range(4)]
    presets = await _preset_stages(conn)
    async with conn.execute(
        "SELECT f.run_id, f.kind, f.polarity, f.reasons_json, f.other_text, "
        "f.created_at FROM feedback f JOIN runs r ON r.id = f.run_id "
        "JOIN chat_sessions s ON s.id = r.session_id WHERE " + _COUNTED,
    ) as cur:
        rows = await _without_fork_copies(conn, list(await cur.fetchall()), 0, 1)
    async with conn.execute(
        "SELECT COUNT(*) FROM feedback f JOIN runs r ON r.id = f.run_id "
        "JOIN chat_sessions s ON s.id = r.session_id WHERE s.deleted_at IS NULL "
        "AND f.kind = 'implicit' AND f.status = 'needs_confirmation' AND "
        + _NOT_RATED,
    ) as cur:
        pending = int((await cur.fetchone() or (0,))[0])

    explicit = {"up": 0, "down": 0}
    implicit_neg = 0
    runs: set[int] = set()
    stage_explicit: Counter[str] = Counter()
    stage_implicit: Counter[str] = Counter()
    weekly: dict[str, list[int]] = {st: [0, 0, 0, 0] for st in STAGES}
    other: Counter[str] = Counter()
    unattributed = {"explicit": 0, "implicit": 0}
    for run_id, kind, polarity, reasons_json, other_text, created_at in rows:
        runs.add(int(run_id))
        if kind == "explicit":
            explicit[str(polarity)] += 1
        if polarity != "down":
            continue
        if kind == "implicit":
            implicit_neg += 1
        reasons = json.loads(reasons_json or "[]")
        bucket = None
        try:
            m = _monday(datetime.fromisoformat(str(created_at)).date())
            if m in week_starts:
                bucket = week_starts.index(m)
        except ValueError:
            pass
        stages_hit = _row_stages(reasons, presets)
        if not stages_hit:
            unattributed[str(kind)] += 1
        for st in stages_hit:
            (stage_explicit if kind == "explicit" else stage_implicit)[st] += 1
            if bucket is not None:
                weekly[st][bucket] += 1
        text = (other_text or "").strip().lower()
        if kind == "explicit" and text:
            other[text] += 1

    total_explicit = explicit["up"] + explicit["down"]
    return FeedbackSummary(
        kpis=Kpis(
            answers_with_feedback=len(runs),
            approval_rate=(explicit["up"] / total_explicit) if total_explicit else None,
            negatives_explicit=explicit["down"],
            negatives_implicit=implicit_neg,
            pending=pending,
        ),
        stages=[
            StageSummary(
                stage=st,
                explicit=stage_explicit[st], implicit=stage_implicit[st],
                weekly=weekly[st], delta=weekly[st][3] - weekly[st][2],
            )
            for st in STAGES
        ],
        other_reasons=[
            OtherReason(text=t, count=c)
            for t, c in sorted(other.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        unattributed=Unattributed(**unattributed),
    )


async def list_cases(
    conn: aiosqlite.Connection, stage: Stage | None = None,
) -> list[FeedbackCase]:
    """Negative feedback rows (newest first), optionally only those with a
    reason in ``stage``. The question is the run's first user message."""
    presets = await _preset_stages(conn)
    async with conn.execute(
        "SELECT f.id, f.run_id, r.session_id, f.kind, f.reasons_json, "
        "f.created_at, "
        "(SELECT m.content FROM messages m WHERE m.run_id = f.run_id "
        " AND m.role = 'user' ORDER BY m.id LIMIT 1) "
        "FROM feedback f JOIN runs r ON r.id = f.run_id "
        "JOIN chat_sessions s ON s.id = r.session_id "
        "WHERE f.polarity = 'down' AND " + _COUNTED + " ORDER BY f.id DESC",
    ) as cur:
        rows = await _without_fork_copies(conn, list(await cur.fetchall()), 1, 3)
    out: list[FeedbackCase] = []
    for fid, run_id, sid, kind, reasons_json, created_at, question in rows:
        if stage is not None and stage not in _row_stages(
            json.loads(reasons_json or "[]"), presets,
        ):
            continue
        out.append(FeedbackCase(
            feedback_id=fid, run_id=run_id, session_id=sid, question=question or "",
            kind=kind, created_at=str(created_at),
        ))
    return out


async def list_presets(conn: aiosqlite.Connection) -> list[Preset]:
    async with conn.execute(
        "SELECT label, stage FROM feedback_presets ORDER BY id",
    ) as cur:
        return [Preset(label=r[0], stage=r[1]) for r in await cur.fetchall()]


async def create_preset(conn: aiosqlite.Connection, label: str, stage: str) -> bool:
    """Insert a preset; ``False`` if the label is taken (built-in reasons and
    ``other`` count as taken). Collisions ignore case and surrounding space,
    compared in Python with ``casefold`` so non-ASCII case variants collide
    too (SQLite's ``lower`` is ASCII-only)."""
    norm = label.strip().casefold()
    if norm in {r.casefold() for r in (*REASONS, "other")}:
        return False
    async with write_transaction(conn):
        async with conn.execute("SELECT label FROM feedback_presets") as cur:
            taken = {str(r[0]).strip().casefold() for r in await cur.fetchall()}
        if norm in taken:
            return False
        try:
            await conn.execute(
                "INSERT INTO feedback_presets (label, stage) VALUES (?, ?)",
                (label, stage),
            )
        except sqlite3.IntegrityError:
            return False
    return True
