"""Append-only outbound audit log (Plan H5a).

Rows carry no foreign keys, so deleting a session never removes them. The only
way rows leave is the retention ``prune``. Callers that have no connection
(background HTTP clients, the LLM adapter) omit ``conn`` and a short-lived one
is opened on the workspace DB.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta

import aiosqlite

from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_lock

logger = logging.getLogger(__name__)

PRUNE_INTERVAL_S = 24 * 60 * 60
ACTORS = ("user", "agent", "system", "deployer")


class InvalidSince(ValueError):
    """The ``since`` filter is not a date / ISO-8601 timestamp."""


@dataclass(frozen=True)
class LogRow:
    id: int
    created_at: str
    owner_id: int
    actor: str
    caller: str
    kind: str
    destination: str
    sent_text: str | None
    original_text: str | None
    decision: str
    stage: str
    reason: str | None
    run_id: int | None
    session_id: int | None


_COLS = ", ".join(f.name for f in fields(LogRow))


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_since(since: str) -> str:
    """Coerce a date, space-separated or ISO timestamp (naive = UTC) to the
    stored ``created_at`` format so string comparison is correct."""
    s = since.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise InvalidSince(
            f"invalid 'since' value {since!r}: use a date or ISO-8601 timestamp"
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


_INSERT_SQL = (
    "INSERT INTO outbound_log (created_at, actor, caller, kind, "
    "destination, sent_text, original_text, decision, stage, reason, "
    "run_id, session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _params(
    actor: str, caller: str, kind: str, destination: str,
    sent_text: str | None, original_text: str | None, decision: str,
    stage: str, reason: str | None, run_id: int | None, session_id: int | None,
) -> tuple[object, ...]:
    if actor not in ACTORS:
        raise ValueError(f"invalid actor {actor!r}: expected one of {ACTORS}")
    return (
        actor, caller, kind, destination, sent_text, original_text,
        decision, stage, reason, run_id, session_id,
    )


async def _insert(
    conn: aiosqlite.Connection, params: tuple[object, ...]
) -> int:
    async with write_lock(conn):
        cur = await conn.execute(_INSERT_SQL, (utc_now(), *params))
        await conn.commit()
        return int(cur.lastrowid or 0)


async def append(
    conn: aiosqlite.Connection | None = None,
    *,
    actor: str,
    caller: str,
    kind: str,
    destination: str,
    decision: str,
    stage: str,
    sent_text: str | None = None,
    original_text: str | None = None,
    reason: str | None = None,
    run_id: int | None = None,
    session_id: int | None = None,
) -> int:
    """Insert one log row and return its id.

    With ``conn=None`` a short-lived connection to the workspace DB is opened.
    """
    params = _params(
        actor, caller, kind, destination, sent_text, original_text,
        decision, stage, reason, run_id, session_id,
    )
    if conn is not None:
        return await _insert(conn, params)
    async with open_db(load_settings().db_path) as own:
        return await _insert(own, params)


def append_sync(
    conn: sqlite3.Connection | None = None,
    *,
    actor: str,
    caller: str,
    kind: str,
    destination: str,
    decision: str,
    stage: str,
    sent_text: str | None = None,
    original_text: str | None = None,
    reason: str | None = None,
    run_id: int | None = None,
    session_id: int | None = None,
) -> int:
    """Blocking twin of :func:`append` for synchronous callers (the arXiv
    client runs in plain code and worker threads, with no event loop).

    Same table, columns and validation; with ``conn=None`` a short-lived
    connection to the workspace DB is opened (busy-timeout bounded)."""
    params = _params(
        actor, caller, kind, destination, sent_text, original_text,
        decision, stage, reason, run_id, session_id,
    )
    db = conn if conn is not None else sqlite3.connect(load_settings().db_path, timeout=10.0)
    try:
        cur = db.execute(_INSERT_SQL, (utc_now(), *params))
        db.commit()
        return int(cur.lastrowid or 0)
    finally:
        if conn is None:
            db.close()


async def query(
    conn: aiosqlite.Connection,
    *,
    decision: str | None = None,
    destination: str | None = None,
    since: str | None = None,
    limit: int = 200,
) -> list[LogRow]:
    where: list[str] = []
    args: list[object] = []
    if decision:
        where.append("decision = ?")
        args.append(decision)
    if destination:
        where.append("destination = ?")
        args.append(destination)
    if since:
        where.append("created_at >= ?")
        args.append(normalize_since(since))
    sql = f"SELECT {_COLS} FROM outbound_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, int(limit)))
    async with conn.execute(sql, args) as cur:
        return [LogRow(*r) for r in await cur.fetchall()]


def to_csv(rows: list[LogRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    names = [f.name for f in fields(LogRow)]
    w.writerow(names)
    for r in rows:
        w.writerow(["" if (v := getattr(r, n)) is None else v for n in names])
    return buf.getvalue()


async def prune(conn: aiosqlite.Connection, retention_days: int) -> int:
    """Delete rows older than ``retention_days``; return how many."""
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(
        timespec="seconds"
    )
    async with write_lock(conn):
        cur = await conn.execute(
            "DELETE FROM outbound_log WHERE created_at < ?", (cutoff,)
        )
        await conn.commit()
        return cur.rowcount


async def run_prune_loop(
    stop: asyncio.Event,
    retention_days: Callable[[], int],
    *,
    interval_s: float = PRUNE_INTERVAL_S,
) -> None:
    """Prune once at startup, then daily, until ``stop`` is set."""
    while not stop.is_set():
        try:
            async with open_db(load_settings().db_path) as conn:
                await prune(conn, retention_days())
        except Exception:
            logger.exception("outbound log prune failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
