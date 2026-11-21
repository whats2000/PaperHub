"""Soft-delete the empty chats left behind by looking (Plan H5b.1).

Before notes were created on first write, showing a new chat in the notes
pane created the chat on the server and an empty note for it: chats the
sidebar never listed, whose notes ``[[`` still offered. This soft-deletes
each chat that has no messages, no attached papers, an empty or missing
note, a title still "New chat", no ``[[`` link pointing at its note and is
not internal -- and its note with it (same tombstone, so the sessions
Restore brings both back). Each removal is printed and logged. Idempotent:
a deleted chat is never listed again.

Usage:
    uv run paperhub-clean-empty-chats            # soft-delete them
    uv run paperhub-clean-empty-chats --dry-run  # list only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.db.migrate import apply_schema, has_table
from paperhub.notes.store import NOTE_HAS_CONTENT_SQL, linked_note_ids

logger = logging.getLogger(__name__)

_CANDIDATES_SQL = f"""
SELECT s.id,
       (SELECT n.id FROM notes n WHERE n.target_kind = 'session'
        AND n.target_id = CAST(s.id AS TEXT) AND n.deleted_at IS NULL) AS note_id,
       (SELECT 1 FROM notes n WHERE n.target_kind = 'session'
        AND n.target_id = CAST(s.id AS TEXT) AND n.deleted_at IS NULL
        AND {NOTE_HAS_CONTENT_SQL}) AS has_content
FROM chat_sessions s
WHERE s.deleted_at IS NULL AND s.internal = 0 AND s.title = 'New chat'
  AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM papers p WHERE p.session_id = s.id)
ORDER BY s.id
"""  # noqa: S608 -- a constant fragment, no user input


async def clean_empty_chats(db_path: Path, *, dry_run: bool) -> list[dict[str, Any]]:
    """One row per ghost chat: ``session_id``, ``note_id`` (None when it has
    no note) and ``status`` ``deleted`` / ``would_delete`` (dry run)."""
    rows: list[dict[str, Any]] = []
    async with open_db(db_path) as conn:
        # The schema first (the notes layer may be new here); a dry run
        # changes nothing, so it only reads what is there.
        if not dry_run:
            await apply_schema(conn)
        if not await has_table(conn, "chat_sessions"):
            return rows
        has_notes = await has_table(conn, "notes")
        if has_notes:
            async with conn.execute(_CANDIDATES_SQL) as cur:
                found = [(int(r[0]), r[1], bool(r[2])) for r in await cur.fetchall()]
            linked = await linked_note_ids(conn)
        else:
            async with conn.execute(
                "SELECT s.id FROM chat_sessions s WHERE s.deleted_at IS NULL "
                "AND s.internal = 0 AND s.title = 'New chat' "
                "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id) "
                "AND NOT EXISTS (SELECT 1 FROM papers p WHERE p.session_id = s.id) "
                "ORDER BY s.id"
            ) as cur:
                found = [(int(r[0]), None, False) for r in await cur.fetchall()]
            linked = set()
        for sid, note_id, has_content in found:
            if has_content or (note_id is not None and int(note_id) in linked):
                continue
            rows.append({"session_id": sid,
                         "note_id": int(note_id) if note_id is not None else None,
                         "status": "would_delete" if dry_run else "deleted"})
        if dry_run or not rows:
            return rows
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        async with write_transaction(conn):
            for r in rows:
                await conn.execute(
                    "UPDATE chat_sessions SET deleted_at = ? WHERE id = ? "
                    "AND deleted_at IS NULL", (now, r["session_id"]))
                if r["note_id"] is not None:
                    await conn.execute(
                        "UPDATE notes SET deleted_at = ? WHERE id = ? "
                        "AND deleted_at IS NULL", (now, r["note_id"]))
        for r in rows:
            logger.info("clean-empty-chats: soft-deleted session=%s note=%s",
                        r["session_id"], r["note_id"])
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rows = asyncio.run(clean_empty_chats(load_settings().db_path, dry_run=args.dry_run))
    for r in rows:
        note = r["note_id"] if r["note_id"] is not None else "-"
        print(f"session {r['session_id']:>5} note {note!s:>5} {r['status']}")
    status = "would_delete" if args.dry_run else "deleted"
    print(f"summary: {status}={len(rows)}" if rows else "summary: no empty chats",
          "(dry run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
