"""Move each idea's free-text note into the notes store (Plan H5b.1).

``ideas.note`` held one note per idea. The notes layer keeps it as an idea note
(``target_kind='idea'``, one paragraph block per paragraph, actor ``system``).
The column stays for one release. Idempotent: an idea that already has an idea
note is reported ``skipped_exists`` and left alone; the text is never lost.

Usage:
    uv run paperhub-migrate-idea-notes            # move the notes
    uv run paperhub-migrate-idea-notes --dry-run  # report only
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.notes.ideas import idea_note_text_or_none, paragraphs, set_idea_note_text


async def migrate(db_path: Path, *, dry_run: bool) -> list[dict[str, Any]]:
    """One row per idea: ``status`` ``migrated`` / ``would_migrate`` (dry run),
    ``skipped_exists`` (already has an idea note) or ``skipped_empty``."""
    rows: list[dict[str, Any]] = []
    async with open_db(db_path) as conn:
        async with conn.execute("SELECT id, note FROM ideas ORDER BY id") as cur:
            ideas = list(await cur.fetchall())
        for iid, note in ideas:
            row: dict[str, Any] = {"idea_id": int(iid)}
            paras = paragraphs(str(note or ""))
            if not paras:
                row["status"] = "skipped_empty"
            elif await idea_note_text_or_none(conn, int(iid)) is not None:
                row["status"] = "skipped_exists"
            else:
                row["paragraphs"] = len(paras)
                row["status"] = "would_migrate" if dry_run else "migrated"
                if not dry_run:
                    await set_idea_note_text(
                        conn, int(iid), str(note), actor="system",
                        summary="moved from ideas.note")
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rows = asyncio.run(migrate(load_settings().db_path, dry_run=args.dry_run))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"{r['idea_id']:>5} {r['status']:<15} paragraphs={r.get('paragraphs', 0)}")
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no ideas",
          "(dry run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
