"""Repair section names that are not headings, in place, for every paper.

Earlier ingests stored raw LaTeX macros (``\\molmoactt``, ``\\modelname{``) and
PDF fragments (code lines, wrapped prose, chart axis ticks) as section names.
This step recomputes each paper's names with the current rules and renames in
place — chunk ids, offsets, text, ``dom_id``, ``page`` and ``bbox`` are never
touched — carrying the rename to sections_json, digest.json, the mechanism
card, ideas and slide grounding (``paperhub.db.section_rename``).

Names come from, per paper:
  * ``rederived`` — LaTeX / PyMuPDF papers: extraction + chunking re-run from
    the cached source; used only when the re-derived spans match the stored
    rows exactly (else ``skipped_geometry_differs``, nothing written);
  * ``stored_rule`` — Marker block-anchored chunks, or a source that cannot be
    re-derived: the heading-shape rules applied to the stored names.

Idempotent: a repaired paper reports ``unchanged``.

Usage:
    uv run paperhub-repair-section-names --dry-run
    uv run paperhub-repair-section-names [--paper-content-id 15]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field

import aiosqlite

from paperhub.cli.reingest import derive_chunks, same_geometry, stored_chunk_rows
from paperhub.config import load_settings
from paperhub.db.section_rename import apply_section_renames, repair_stored_names

_LOG = logging.getLogger("paperhub.repair_section_names")


@dataclass
class Plan:
    status: str  # renamed | unchanged | skipped_no_chunks | skipped_geometry_differs
    method: str = ""
    names: dict[int, str | None] = field(default_factory=dict)
    renames: dict[str | None, str | None] = field(default_factory=dict)


async def plan_paper(conn: aiosqlite.Connection, pcid: int) -> Plan:
    """Compute the repaired name for every chunk of one paper (no writes)."""
    stored = await stored_chunk_rows(conn, pcid)
    if not stored:
        return Plan("skipped_no_chunks")
    async with conn.execute(
        "SELECT id, section FROM chunks WHERE paper_content_id = ? ORDER BY id", (pcid,),
    ) as cur:
        current: list[tuple[int, str | None]] = [
            (int(r[0]), r[1]) for r in await cur.fetchall()
        ]
    new_names: list[str | None]
    derived = None
    if all(r[4] is None for r in stored):
        derived = await derive_chunks(conn, pcid)
    if derived is not None:
        if not same_geometry(stored, derived.chunks):
            return Plan("skipped_geometry_differs", "rederived")
        method = "rederived"
        new_names = [c.section for c in derived.chunks]
    else:
        method = "stored_rule"
        new_names = repair_stored_names([s for _, s in current])
    names = {cid: new for (cid, old), new in zip(current, new_names, strict=True) if new != old}
    renames = {old: new for (cid, old), new in zip(current, new_names, strict=True) if new != old}
    return Plan("renamed" if names else "unchanged", method, names, renames)


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-repair-section-names",
        description="Repair non-heading section names in place (idempotent).",
    )
    parser.add_argument("--paper-content-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    settings = load_settings()
    counts: Counter[str] = Counter()
    async with aiosqlite.connect(settings.db_path) as conn:
        if args.paper_content_id is not None:
            ids = [args.paper_content_id]
        else:
            async with conn.execute("SELECT id FROM paper_content ORDER BY id") as cur:
                ids = [int(r[0]) for r in await cur.fetchall()]
        for pcid in ids:
            try:
                plan = await plan_paper(conn, pcid)
                if plan.status == "renamed" and not args.dry_run:
                    rep = await apply_section_renames(conn, pcid, plan.names)
                    _LOG.info("pcid=%d: applied %s", pcid, rep)
            except Exception:  # noqa: BLE001 — per-paper recovery; reported below
                _LOG.exception("pcid=%d: error", pcid)
                counts["error"] += 1
                continue
            counts[plan.status] += 1
            _LOG.info("pcid=%d: %s (%s)%s", pcid, plan.status, plan.method or "-",
                      f" {len(plan.names)} chunk(s)" if plan.names else "")
            for old, new in plan.renames.items():
                _LOG.info("    %r -> %r", old, new)
    _LOG.info("summary%s: %s", " (dry run)" if args.dry_run else "", dict(sorted(counts.items())))
    return 1 if counts["error"] else 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
