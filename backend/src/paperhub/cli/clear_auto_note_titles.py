"""Clear note titles that were auto-filled from the target (Plan H5b.1).

Notes were created with their paper / chat / idea's title stored as the note
title. A note's title is now the user's text only; the target's title is the
default shown as a placeholder (``default_title``). This sets ``title = ''``
where the stored title equals the target's current title (whitespace
collapsed). Idempotent: an empty title, or one the user typed, is kept.

Usage:
    uv run paperhub-clear-auto-note-titles            # clear them
    uv run paperhub-clear-auto-note-titles --dry-run  # report only
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.db.migrate import has_table
from paperhub.notes.store import _NOTE_COLS, _note, default_title

_TARGET_SQL = {
    "paper": "SELECT 1 FROM paper_content WHERE id = ?",
    "session": "SELECT 1 FROM chat_sessions WHERE id = ?",
    "idea": "SELECT 1 FROM ideas WHERE id = ?",
}


def _norm(s: str) -> str:
    return " ".join(s.split())


async def clear_titles(db_path: Path, *, dry_run: bool) -> list[dict[str, Any]]:
    """One row per note with a title: ``cleared`` / ``would_clear`` (dry run),
    ``kept_user_title`` or ``target_missing``."""
    rows: list[dict[str, Any]] = []
    async with open_db(db_path) as conn:
        if not await has_table(conn, "notes"):
            return rows
        async with conn.execute(
            f"SELECT {_NOTE_COLS} FROM notes WHERE title != '' ORDER BY id"  # noqa: S608
        ) as cur:
            notes = [_note(r) for r in await cur.fetchall()]
        clear: list[int] = []
        for note in notes:
            row: dict[str, Any] = {"note_id": note.id, "target": f"{note.target_kind}:{note.target_id}"}
            sql = _TARGET_SQL.get(note.target_kind)
            exists = None
            if sql is not None and note.target_id.isdigit():
                async with conn.execute(sql, (int(note.target_id),)) as cur:
                    exists = await cur.fetchone()
            if exists is None:
                row["status"] = "target_missing"
            elif _norm(note.title) == _norm(await default_title(conn, note)):
                row["status"] = "would_clear" if dry_run else "cleared"
                clear.append(note.id)
            else:
                row["status"] = "kept_user_title"
            rows.append(row)
        if clear and not dry_run:
            async with write_transaction(conn):
                await conn.executemany(
                    "UPDATE notes SET title = '' WHERE id = ?", [(i,) for i in clear])
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rows = asyncio.run(clear_titles(load_settings().db_path, dry_run=args.dry_run))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"{r['note_id']:>5} {r['status']:<16} {r['target']}")
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no titled notes",
          "(dry run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
