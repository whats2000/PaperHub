"""Comment threads on note text (H5b.1). The agent only reads them (as
``<user_comment>`` context); only the user creates, replies to, resolves or
deletes. Thread text is not note text: it never reaches ``note_blocks``."""
from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from paperhub.db.connection import write_lock
from paperhub.notes.store import list_blocks

_ALPHABET = string.ascii_lowercase + string.digits
STATUSES = ("open", "resolved", "deleted")


class CommentInputError(ValueError):
    """The request names something invalid (block, empty text)."""


class CommentConflict(RuntimeError):
    """A deleted thread takes no replies and only reopens."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(text: str) -> str:
    text = text.strip()
    if not text:
        raise CommentInputError("comment text is empty")
    return text


async def _items(conn: aiosqlite.Connection, ids: list[str],
                 ) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" for _ in ids)
    async with conn.execute(
        "SELECT id, comment_id, text, created_at FROM note_comment_items "  # noqa: S608
        f"WHERE comment_id IN ({marks}) ORDER BY id", ids,
    ) as cur:
        for r in await cur.fetchall():
            out[r[1]].append({"id": r[0], "text": r[2], "created_at": r[3]})
    return out


_COLS = "id, note_id, block_id, quote, status, created_at"


async def _threads(conn: aiosqlite.Connection, where: str,
                   params: tuple[Any, ...]) -> list[dict[str, Any]]:
    async with conn.execute(
        f"SELECT {_COLS} FROM note_comments WHERE {where} "  # noqa: S608
        "ORDER BY created_at, rowid", params,
    ) as cur:
        rows = await cur.fetchall()
    items = await _items(conn, [r[0] for r in rows])
    return [{"id": r[0], "note_id": r[1], "block_id": r[2], "quote": r[3],
             "status": r[4], "created_at": r[5], "items": items[r[0]]}
            for r in rows]


async def get_comment(conn: aiosqlite.Connection, cid: str,
                      ) -> dict[str, Any] | None:
    found = await _threads(conn, "id = ?", (cid,))
    return found[0] if found else None


async def list_comments(conn: aiosqlite.Connection, note_id: int,
                        *, open_only: bool = False) -> list[dict[str, Any]]:
    """The note's threads (never deleted ones), oldest first."""
    status = "status = 'open'" if open_only else "status IN ('open', 'resolved')"
    return await _threads(conn, f"note_id = ? AND {status}", (note_id,))


async def create_comment(conn: aiosqlite.Connection, note_id: int, *,
                         block_id: str, quote: str, text: str,
                         ) -> dict[str, Any]:
    text = _clean(text)
    if block_id not in {b.id for b in await list_blocks(conn, note_id)}:
        raise CommentInputError(f"block {block_id} is not in note {note_id}")
    cid = "c_" + "".join(secrets.choice(_ALPHABET) for _ in range(10))
    now = _now()
    async with write_lock(conn):
        await conn.execute(
            "INSERT INTO note_comments (id, note_id, block_id, quote, status, "
            "created_at) VALUES (?, ?, ?, ?, 'open', ?)",
            (cid, note_id, block_id, quote, now))
        await conn.execute(
            "INSERT INTO note_comment_items (comment_id, text, created_at) "
            "VALUES (?, ?, ?)", (cid, text, now))
        await conn.commit()
    thread = await get_comment(conn, cid)
    assert thread is not None
    return thread


async def add_item(conn: aiosqlite.Connection, cid: str, text: str,
                   ) -> dict[str, Any]:
    text = _clean(text)
    thread = await get_comment(conn, cid)
    if thread is None:
        raise LookupError(f"comment {cid} not found")
    if thread["status"] == "deleted":
        raise CommentConflict("this comment was deleted")
    async with write_lock(conn):
        await conn.execute(
            "INSERT INTO note_comment_items (comment_id, text, created_at) "
            "VALUES (?, ?, ?)", (cid, text, _now()))
        await conn.commit()
    thread = await get_comment(conn, cid)
    assert thread is not None
    return thread


async def set_status(conn: aiosqlite.Connection, cid: str, status: str,
                     ) -> dict[str, Any]:
    if status not in STATUSES:
        raise CommentInputError(f"unknown status {status!r}")
    thread = await get_comment(conn, cid)
    if thread is None:
        raise LookupError(f"comment {cid} not found")
    # A deleted thread only comes back as open: that is the Undo of Delete.
    if thread["status"] == "deleted" and status not in ("deleted", "open"):
        raise CommentConflict("a deleted comment can only be reopened")
    async with write_lock(conn):
        await conn.execute("UPDATE note_comments SET status = ? WHERE id = ?",
                           (status, cid))
        await conn.commit()
    thread["status"] = status
    return thread
