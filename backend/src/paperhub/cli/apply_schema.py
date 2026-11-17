"""Bring the workspace DB's schema up to date (the routine the backend runs
at startup).

Step 0 of ``scripts/reingest_all_papers.ps1``: the upgrade runs on the
library as it is, before the new backend has ever started on it, so tables
added since (the H5b notes layer) may not exist yet. Idempotent and
additive.

Usage:
    uv run paperhub-apply-schema            # apply
    uv run paperhub-apply-schema --dry-run  # name the missing tables only
"""
from __future__ import annotations

import argparse
import asyncio
import re
from importlib.resources import files

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema, has_table

_TABLE = re.compile(r"CREATE (?:VIRTUAL )?TABLE IF NOT EXISTS (\w+)", re.I)


async def _amain() -> int:
    ap = argparse.ArgumentParser(prog="paperhub-apply-schema",
                                 description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sql = (files("paperhub.db") / "schema.sql").read_text(encoding="utf-8")
    async with open_db(load_settings().db_path) as conn:
        missing = [t for t in dict.fromkeys(_TABLE.findall(sql))
                   if not await has_table(conn, t)]
        if not args.dry_run:
            await apply_schema(conn)
    verb = "would add" if args.dry_run else "added"
    print(f"summary: {verb} {len(missing)} table(s)"
          + (f": {', '.join(missing)}" if missing else "")
          + (" (dry run)" if args.dry_run else ""))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
