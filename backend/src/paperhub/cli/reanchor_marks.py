"""Re-anchor reading marks to each paper's current chunks (Plan H5b.1).

A re-ingest gives a paper new chunk ids and offsets. This re-finds every HTML
mark's quote in the paper's rendered HTML (the text the reader quoted; run it
after ``paperhub-rerender-html``) and rewrites its chunk ids and offsets; a quote that
cannot be found stays in the note, flagged lost (nothing is dropped). Section
headings whose section no longer exists are flagged too. Idempotent: a second
run changes nothing.

Usage:
    uv run paperhub-reanchor-marks            # rewrite in place
    uv run paperhub-reanchor-marks --dry-run  # report only
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema, has_table
from paperhub.notes.anchors import reanchor_paper


async def migrate(db_path: Path, *, dry_run: bool) -> list[dict[str, Any]]:
    """One row per paper that has marks: ``paper_content_id`` + counts
    (``ok``, ``moved``, ``lost``, ``partly_lost``)."""
    rows: list[dict[str, Any]] = []
    async with open_db(db_path) as conn:
        # Run before the new backend ever started here: a real run brings the
        # schema up to date; a dry run changes nothing (no table, no marks).
        if not dry_run:
            await apply_schema(conn)
        elif not await has_table(conn, "note_marks"):
            return rows
        async with conn.execute(
            "SELECT DISTINCT paper_content_id FROM note_marks "
            "ORDER BY paper_content_id",
        ) as cur:
            ids = [int(r[0]) for r in await cur.fetchall()]
        for pcid in ids:
            rows.append({"paper_content_id": pcid,
                         **await reanchor_paper(conn, pcid, dry_run=dry_run)})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rows = asyncio.run(migrate(load_settings().db_path, dry_run=args.dry_run))
    totals = {"ok": 0, "moved": 0, "lost": 0, "partly_lost": 0}
    for r in rows:
        for k in totals:
            totals[k] += r[k]
        print(f"{r['paper_content_id']:>5} ok={r['ok']} moved={r['moved']} "
              f"lost={r['lost']} partly_lost={r['partly_lost']}")
    print("summary:", f"papers={len(rows)}",
          ", ".join(f"{k}={v}" for k, v in totals.items()),
          "(dry run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
