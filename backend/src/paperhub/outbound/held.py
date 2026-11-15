"""Held outbound requests awaiting a human decision (Plan H5a).

One held card in the chat can cover several rows: the same text held for
different destinations in one run. A decision on any row of the card applies
to every pending row of it. A sent row's approval is spent once
(``consumed_at``) when the follow-up turn sends that exact text.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta

import aiosqlite

from paperhub.db.connection import write_lock, write_transaction
from paperhub.outbound import log
from paperhub.outbound.log import utc_now


@dataclass(frozen=True)
class HeldRow:
    id: str
    created_at: str | None
    owner_id: int
    caller: str | None
    kind: str | None
    destination: str | None
    text: str
    reason: str | None
    unattended: bool
    run_id: int | None
    session_id: int | None
    status: str
    sent_text: str | None
    log_id: int | None
    consumed_at: str | None = None
    reserved_at: str | None = None
    sending_at: str | None = None


_COLS = ", ".join(f.name for f in fields(HeldRow))

# A row nobody may decide any more: its turn was stopped or its chat deleted.
_DEAD_SQL = (
    "(run_id IN (SELECT id FROM runs WHERE status = 'cancelled') OR "
    "session_id IN (SELECT id FROM chat_sessions WHERE deleted_at IS NOT NULL))"
)


async def create(
    conn: aiosqlite.Connection,
    *,
    caller: str,
    kind: str,
    destination: str,
    text: str,
    reason: str | None,
    unattended: bool,
    run_id: int | None,
    session_id: int | None,
) -> str:
    held_id = uuid.uuid4().hex
    async with write_lock(conn):
        await conn.execute(
            "INSERT INTO held_requests (id, created_at, caller, kind, "
            "destination, text, reason, unattended, run_id, session_id, "
            "status) VALUES (?,?,?,?,?,?,?,?,?,?,'pending')",
            (held_id, utc_now(), caller, kind, destination, text, reason,
             int(unattended), run_id, session_id),
        )
        await conn.commit()
    return held_id


def _row(r: tuple[object, ...]) -> HeldRow:
    vals = list(r)
    vals[8] = bool(vals[8])
    return HeldRow(*vals)  # type: ignore[arg-type]


async def list_pending(conn: aiosqlite.Connection) -> list[HeldRow]:
    """Pending rows that can still be decided (not of a stopped run or a
    deleted chat)."""
    async with conn.execute(
        f"SELECT {_COLS} FROM held_requests WHERE status = 'pending' "  # noqa: S608
        f"AND NOT ({_STALE_SQL}) ORDER BY created_at, id"
    ) as cur:
        return [_row(tuple(r)) for r in await cur.fetchall()]


async def count_pending(conn: aiosqlite.Connection) -> int:
    async with conn.execute(
        "SELECT COUNT(*) FROM held_requests WHERE status = 'pending' "  # noqa: S608
        f"AND NOT ({_STALE_SQL})"
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def get(conn: aiosqlite.Connection, held_id: str) -> HeldRow | None:
    async with conn.execute(
        f"SELECT {_COLS} FROM held_requests WHERE id = ?", (held_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row(tuple(row)) if row is not None else None


async def _claim(
    conn: aiosqlite.Connection, anchor: HeldRow, status: str,
    sent_text: str | None, *, allow_dead: bool = False,
) -> list[HeldRow]:
    """In ONE write transaction: move the anchor, then the other pending
    rows of its card, from pending to ``status``. The anchor goes first;
    if it is no longer pending (a racing decision won), or its turn was
    stopped or its chat deleted meanwhile (unless ``allow_dead``: the
    system discarding such rows), nothing changes."""
    live = "" if allow_dead else f" AND NOT {_DEAD_SQL}"
    sending_at = utc_now() if status == "sending" else None
    async with write_transaction(conn):
        cur = await conn.execute(
            "UPDATE held_requests SET status = ?, sent_text = ?, sending_at = ? "  # noqa: S608
            f"WHERE id = ? AND status = 'pending'{live}",
            (status, sent_text, sending_at, anchor.id))
        if cur.rowcount == 0:
            return []
        claimed = [anchor]
        if anchor.run_id is not None:
            async with conn.execute(
                f"SELECT {_COLS} FROM held_requests WHERE run_id = ? AND text = ? "  # noqa: S608
                f"AND status = 'pending' AND id <> ?{live} ORDER BY created_at, id",
                (anchor.run_id, anchor.text, anchor.id),
            ) as c:
                rest = [_row(tuple(r)) for r in await c.fetchall()]
            for row in rest:
                await conn.execute(
                    "UPDATE held_requests SET status = ?, sent_text = ?, "
                    "sending_at = ? WHERE id = ?",
                    (status, sent_text, sending_at, row.id))
            claimed += rest
    return claimed


async def _set_status(
    conn: aiosqlite.Connection, rows: list[HeldRow], status: str,
) -> None:
    """Finish an in-flight resend: ``sent``, or back to ``pending``."""
    clear = ", sent_text = NULL" if status == "pending" else ""
    async with write_transaction(conn):
        for row in rows:
            await conn.execute(
                f"UPDATE held_requests SET status = ?, sending_at = NULL{clear} "  # noqa: S608
                "WHERE id = ? AND status = 'sending'", (status, row.id))


async def _log_decision(
    conn: aiosqlite.Connection, row: HeldRow, status: str, *, actor: str,
    stage: str, sent_text: str | None, reason: str | None,
) -> None:
    sent = status == "sent"
    log_id = await log.append(
        conn, actor=actor, caller=row.caller or "", kind=row.kind or "",
        destination=row.destination or "",
        decision="asked_sent" if sent else "asked_discarded", stage=stage,
        sent_text=sent_text if sent else None,
        original_text=row.text if (not sent or sent_text != row.text) else None,
        reason=reason, run_id=row.run_id, session_id=row.session_id,
    )
    async with write_lock(conn):
        await conn.execute(
            "UPDATE held_requests SET log_id = ? WHERE id = ?", (log_id, row.id))
        await conn.commit()


Resender = Callable[["HeldRow", str], Awaitable[None]]


class ResendFailed(Exception):
    """An unattended caller's resender failed; the card is pending again."""


async def decide(
    conn: aiosqlite.Connection, anchor: HeldRow, status: str, *,
    actor: str, sent_text: str | None = None, stage: str = "user",
    reason: str | None = None, resend: Resender | None = None,
    allow_dead: bool = False,
) -> list[str]:
    """Send (``status="sent"`` with ``sent_text``) or discard every pending
    row of ``anchor``'s card; returns the ids resolved, empty when the
    anchor was already decided (then nothing is decided).

    ``resend`` (an unattended row's resender) runs after the claim, so two
    racing sends cannot both run it. Meanwhile the rows are ``sending``
    (not pending, not yet sent); success makes them ``sent`` and logs them;
    failure puts them back to pending, logs nothing and raises
    :class:`ResendFailed`. A crash mid-resend leaves ``sending`` rows that
    :func:`sweep_stale` returns to pending after ``SENDING_TIMEOUT``."""
    claim_as = "sending" if resend is not None else status
    rows = await _claim(conn, anchor, claim_as, sent_text, allow_dead=allow_dead)
    if not rows:
        return []
    if resend is not None:
        try:
            await resend(anchor, sent_text or anchor.text)
        except Exception as exc:
            await _set_status(conn, rows, "pending")
            raise ResendFailed(type(exc).__name__) from exc
        await _set_status(conn, rows, status)
    for row in rows:
        await _log_decision(conn, row, status, actor=actor, stage=stage,
                            sent_text=sent_text, reason=reason)
    return [r.id for r in rows]


async def discard_pending(
    conn: aiosqlite.Connection, *, run_id: int | None = None,
    session_id: int | None = None, reason: str,
) -> list[str]:
    """Discard (actor ``system``) every pending row of a stopped run or a
    deleted chat."""
    if run_id is None and session_id is None:
        return []
    col, val = ("run_id", run_id) if run_id is not None else ("session_id", session_id)
    async with conn.execute(
        f"SELECT {_COLS} FROM held_requests WHERE {col} = ? AND status = 'pending' "  # noqa: S608
        "ORDER BY created_at, id", (val,),
    ) as cur:
        rows = [_row(tuple(r)) for r in await cur.fetchall()]
    done: list[str] = []
    for r in rows:
        done += await decide(conn, r, "discarded", actor="system",
                             stage="system", reason=reason, allow_dead=True)
    return done


# Pending rows left after Stop / delete swept (a stopped run can still
# write one).
_STALE_SQL = f"status = 'pending' AND {_DEAD_SQL}"


SENDING_TIMEOUT = timedelta(minutes=5)


async def sweep_stale(conn: aiosqlite.Connection) -> list[str]:
    """Discard (actor ``system``) pending rows of stopped runs and deleted
    chats; returns their ids. Also returns ``sending`` rows older than
    ``SENDING_TIMEOUT`` (a resend that crashed) to pending."""
    cutoff = (datetime.now(UTC) - SENDING_TIMEOUT).isoformat(timespec="seconds")
    async with write_lock(conn):
        await conn.execute(
            "UPDATE held_requests SET status = 'pending', sent_text = NULL, "
            "sending_at = NULL WHERE status = 'sending' AND sending_at < ?",
            (cutoff,))
        await conn.commit()
    async with conn.execute(
        f"SELECT {_COLS} FROM held_requests WHERE {_STALE_SQL} "  # noqa: S608
        "ORDER BY created_at, id",
    ) as cur:
        rows = [_row(tuple(r)) for r in await cur.fetchall()]
    done: list[str] = []
    for r in rows:
        done += await decide(conn, r, "discarded", actor="system", stage="system",
                             reason="its chat turn was stopped or its chat deleted",
                             allow_dead=True)
    return done


_APPROVAL_SQL = (
    "FROM held_requests h JOIN held_requests a ON a.id = ? "
    "WHERE h.status = 'sent' AND h.consumed_at IS NULL AND h.sent_text = ? "
    "AND h.session_id = ? AND (h.id = a.id OR (a.run_id IS NOT NULL "
    "AND h.run_id = a.run_id AND h.text = a.text))"
)


async def approved_rows(
    conn: aiosqlite.Connection, held_id: str, *, text: str, session_id: int | None,
) -> list[HeldRow]:
    """The unspent approvals ``held_id``'s card grants for ``text`` in this
    chat (one per destination the user sent it to)."""
    if session_id is None:
        return []
    cols = ", ".join(f"h.{f.name}" for f in fields(HeldRow))
    async with conn.execute(
        f"SELECT {cols} {_APPROVAL_SQL} ORDER BY h.created_at, h.id",  # noqa: S608
        (held_id, text, session_id),
    ) as cur:
        return [_row(tuple(r)) for r in await cur.fetchall()]


async def consume_approval(
    conn: aiosqlite.Connection, held_id: str, *, kind: str, destination: str,
    text: str, session_id: int | None,
) -> str | None:
    """Spend the approval ``held_id`` grants for ``text`` to ``destination``:
    a sent, unconsumed row of that card (``held_id`` itself or a sibling)
    whose sent text is exactly ``text``, in the same chat. Returns the row
    spent, or None (no approval: the request takes the normal stages)."""
    if session_id is None:
        return None
    async with write_lock(conn):
        async with conn.execute(
            f"SELECT h.id {_APPROVAL_SQL} AND h.kind = ? AND h.destination = ? "  # noqa: S608
            "ORDER BY h.id = a.id DESC, h.created_at, h.id LIMIT 1",
            (held_id, text, session_id, kind, destination),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        cur2 = await conn.execute(
            "UPDATE held_requests SET consumed_at = ? "
            "WHERE id = ? AND consumed_at IS NULL", (utc_now(), row[0]))
        await conn.commit()
        return str(row[0]) if cur2.rowcount else None


async def reserve_followup(
    conn: aiosqlite.Connection, held_id: str, *, session_id: int | None,
) -> HeldRow | None:
    """Reserve the one follow-up turn of an approved card, atomically: the
    row must be sent by the user in this chat, unspent, and no turn may
    have reserved its card yet. Returns the row, or None (a second turn,
    a spent or foreign approval)."""
    if session_id is None:
        return None
    async with write_transaction(conn):
        row = await get(conn, held_id)
        if (row is None or row.session_id != session_id or row.status != "sent"
                or row.consumed_at is not None or not row.sent_text):
            return None
        async with conn.execute(
            f"SELECT {_DEAD_SQL} FROM held_requests WHERE id = ?", (held_id,),  # noqa: S608
        ) as cur:
            dead = await cur.fetchone()
        if dead is not None and dead[0]:
            return None  # its turn was stopped or its chat deleted
        where, params = (("run_id = ? AND text = ?", (row.run_id, row.text))
                         if row.run_id is not None else ("id = ?", (row.id,)))
        async with conn.execute(
            f"SELECT COUNT(*) FROM held_requests WHERE {where} "  # noqa: S608
            "AND reserved_at IS NOT NULL", params,
        ) as cur:
            taken = await cur.fetchone()
        if taken is not None and int(taken[0]) > 0:
            return None
        await conn.execute(
            f"UPDATE held_requests SET reserved_at = ? WHERE {where} "  # noqa: S608
            "AND status = 'sent'", (utc_now(), *params))
    return row


async def release_followup(conn: aiosqlite.Connection, held_id: str) -> bool:
    """Undo :func:`reserve_followup` when the follow-up turn ended without
    sending the approved text (no row of the card spent). True if released."""
    async with write_transaction(conn):
        row = await get(conn, held_id)
        if row is None:
            return False
        where, params = (("run_id = ? AND text = ?", (row.run_id, row.text))
                         if row.run_id is not None else ("id = ?", (row.id,)))
        async with conn.execute(
            f"SELECT COUNT(*) FROM held_requests WHERE {where} "  # noqa: S608
            "AND consumed_at IS NOT NULL", params,
        ) as cur:
            spent = await cur.fetchone()
        if spent is not None and int(spent[0]) > 0:
            return False
        cur2 = await conn.execute(
            f"UPDATE held_requests SET reserved_at = NULL WHERE {where} "  # noqa: S608
            "AND reserved_at IS NOT NULL", params)
        return cur2.rowcount > 0


_RESENDERS: dict[str, Resender] = {}


def register_resender(caller: str, fn: Resender) -> None:
    """How an unattended caller (no chat to continue in) sends a held
    request the user approved: ``fn(row, text)``."""
    _RESENDERS[caller] = fn


def resender(caller: str | None) -> Resender | None:
    return _RESENDERS.get(caller or "")
