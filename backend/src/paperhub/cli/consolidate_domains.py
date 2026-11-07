"""Fold near-duplicate domain tags into their canonical field (QA I8).

Cards built before the canonical field list tagged "Audio" and "Audio
Processing", "Genomics" and "Functional Genomics" as separate domains, and up
to three per document. This folds every auto-tagged legacy domain into its
canonical row (``ideation.domains.display_domain``) and caps each document's
auto tags at ``MAX_DOMAINS``. User-curated domains and manual tags are left
alone. Deterministic and idempotent; run it after ``paperhub-backfill-cards``
(a card rebuild re-tags documents with the current card prompt).

Usage:
    uv run paperhub-consolidate-domains            # rewrite in place
    uv run paperhub-consolidate-domains --dry-run  # report only
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.ideation.domains import consolidate_domains


async def _table(conn: aiosqlite.Connection) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    async with conn.execute(
        "SELECT dd.paper_content_id, d.name FROM document_domains dd "
        "JOIN domains d ON d.id = dd.domain_id ORDER BY dd.paper_content_id, dd.rowid",
    ) as cur:
        for pid, name in await cur.fetchall():
            out.setdefault(int(pid), []).append(str(name))
    return out


async def run(db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    async with open_db(db_path) as conn:
        before = await _table(conn)
        plan = await consolidate_domains(conn, dry_run=dry_run)
        after = await _table(conn)
        async with conn.execute(
            "SELECT COUNT(*) FROM domains WHERE EXISTS "
            "(SELECT 1 FROM document_domains WHERE domain_id = domains.id)",
        ) as cur:
            row = await cur.fetchone()
    return {**plan, "before": before, "after": after,
            "domains_in_use": int(row[0]) if row else 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = asyncio.run(run(load_settings().db_path, dry_run=args.dry_run))
    for old, new in sorted(out["renamed_or_merged"].items()):
        print(f"fold   {old!r} -> {new!r}")
    for pid, dropped in sorted(out["capped"].items(), key=lambda kv: int(kv[0])):
        print(f"cap    {pid:>5} drops {dropped}")
    print(f"summary: folded={len(out['renamed_or_merged'])} capped={len(out['capped'])} "
          f"domains_in_use={out['domains_in_use']}" + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
