"""Move reading marks to kind + style (Plan H5b.1, 2026-11-19).

A mark's kind now says WHAT was marked (text / figure / equation / region)
and a text mark's style how it is drawn. Old rows: a highlight becomes a
text mark drawn as a highlight (colour kept); a quote becomes a text mark
drawn as an underline with no colour. The marks' note blocks follow, and
the colour palette gets each colour's hex and order. Rebuilds note_marks
(its CHECK changes), so stop the backend and back up the DB first; the
backend never runs this on start. Idempotent: a second run changes nothing.

Usage:
    uv run paperhub-migrate-mark-styles --dry-run  # counts only
    uv run paperhub-migrate-mark-styles            # migrate
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema, migrate_mark_styles


async def migrate(db_path: Path, *, dry_run: bool) -> dict[str, int]:
    async with open_db(db_path) as conn:
        if not dry_run:
            await apply_schema(conn)
        return await migrate_mark_styles(conn, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = asyncio.run(migrate(load_settings().db_path, dry_run=args.dry_run))
    verb = "would change" if args.dry_run else "changed"
    print(f"summary: {verb} highlight->text/highlight={out['highlight']} "
          f"quote->text/underline={out['quote']} note_blocks={out['blocks']} "
          f"colours_filled={out['colors']} table_rebuilt={out['rebuilt']}"
          + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
