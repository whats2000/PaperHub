"""What a chat's agent has seen of each note (H5b.1).

Two marks per chat session:

- ``note_agent_reads``: the agent's contact with a note (any read, search
  or write): ``first_revision`` at first contact, ``revision`` at the last.
- ``note_agent_block_reads``: the revision at which the agent saw each block
  (a ``read_note`` page, a search hit shown whole, a block it wrote).

A user change to block ``b`` is unseen when its revision is after the
revision the agent saw ``b`` at, or, for a block the agent never saw, after
its first contact with the note. ``<notes_changes>`` and ``list_notes``
report exactly those, so a search hit or one page never hides an edit
elsewhere in the note.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

import aiosqlite

from paperhub.db.connection import write_transaction


async def record_contact(
    conn: aiosqlite.Connection, session_id: int, revisions: dict[int, int],
) -> None:
    """Note the agent touched each note at the given revision."""
    if not revisions:
        return
    now = datetime.now(UTC).isoformat()
    async with write_transaction(conn):
        for note_id, rev in revisions.items():
            await conn.execute(
                "INSERT INTO note_agent_reads (session_id, note_id, revision, "
                "first_revision, at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (session_id, note_id) DO UPDATE SET "
                "revision = excluded.revision, at = excluded.at",
                (session_id, note_id, rev, rev, now))


async def record_blocks(
    conn: aiosqlite.Connection, session_id: int, note_id: int,
    block_ids: Iterable[str], revision: int,
) -> None:
    """Note the agent saw these blocks as they were at ``revision``."""
    ids = list(dict.fromkeys(block_ids))
    if not ids:
        return
    now = datetime.now(UTC).isoformat()
    async with write_transaction(conn):
        await conn.executemany(
            "INSERT INTO note_agent_block_reads (session_id, note_id, block_id, "
            "revision, at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (session_id, note_id, block_id) DO UPDATE SET "
            "revision = MAX(revision, excluded.revision), at = excluded.at",
            [(session_id, note_id, b, revision, now) for b in ids])


async def contacts(
    conn: aiosqlite.Connection, session_id: int,
) -> dict[int, tuple[int, int]]:
    """``note_id -> (first revision, last revision)`` the agent touched."""
    async with conn.execute(
        "SELECT note_id, COALESCE(first_revision, revision), revision "
        "FROM note_agent_reads WHERE session_id = ?", (session_id,),
    ) as cur:
        return {int(r[0]): (int(r[1]), int(r[2])) for r in await cur.fetchall()}


async def unseen_user_changes(
    conn: aiosqlite.Connection, session_id: int, note_id: int,
    first_revision: int, limit: int,
) -> list[tuple[str, str]]:
    """``(block id, last change time)`` of the user's changes the agent has
    not seen, in the order they were first made (at most ``limit``)."""
    async with conn.execute(
        "SELECT h.block_id, MAX(h.created_at) FROM note_block_history h "
        "LEFT JOIN note_agent_block_reads r ON r.session_id = ? "
        "AND r.note_id = h.note_id AND r.block_id = h.block_id "
        "WHERE h.note_id = ? AND h.actor = 'user' AND h.revision > ? "
        "AND (r.revision IS NULL OR h.revision > r.revision) "
        "GROUP BY h.block_id ORDER BY MIN(h.id) LIMIT ?",
        (session_id, note_id, first_revision, limit),
    ) as cur:
        return [(str(r[0]), str(r[1])) for r in await cur.fetchall()]
