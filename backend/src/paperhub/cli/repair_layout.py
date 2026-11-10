"""Repair figure/table layout indexes whose chunk ids no longer exist.

``paper_content.layout_json`` maps each figure/table label to the chunk that
holds it. An older ``paperhub-reingest`` replaced some papers' Marker chunks
with PyMuPDF chunks without rebuilding that index, so its chunk ids point at
deleted rows and ``read_layout_object`` fails with "chunk ... is missing". This
step re-points each stale entry at the paper's chunk that contains its
caption; an entry whose caption is found in no chunk (or in none) loses its
chunk id, which the tools show as "no chunk to read" instead of an error.

Idempotent: a paper whose entries all point at its own chunks reports
``unchanged``; papers without a layout index report ``no_layout``.

Usage:
    uv run paperhub-repair-layout --dry-run
    uv run paperhub-repair-layout [--paper-content-id 20]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass

import aiosqlite

from paperhub.config import load_settings

_LOG = logging.getLogger("paperhub.repair_layout")
# Leading caption characters matched against chunk text (whitespace folded).
CAPTION_PREFIX = 60


@dataclass
class LayoutPlan:
    # updated | unchanged | no_layout
    status: str
    remapped: int = 0
    dropped: int = 0
    layout: list[dict[str, object]] | None = None


def _fold(text: str) -> str:
    return " ".join(text.split())


def remap_layout(
    layout: list[dict[str, object]], chunks: list[tuple[int, str]],
) -> tuple[list[dict[str, object]], int, int]:
    """``(new layout, remapped, dropped)``: entries whose ``chunk_id`` is not
    one of ``chunks`` move to the first chunk containing their caption, else
    lose the id. Entries already pointing at one of ``chunks`` are kept."""
    own = {cid for cid, _ in chunks}
    folded = [(cid, _fold(text)) for cid, text in chunks]
    out: list[dict[str, object]] = []
    remapped = dropped = 0
    for entry in layout:
        cid = entry.get("chunk_id")
        if not isinstance(cid, int) or cid in own:
            out.append(entry)
            continue
        caption = _fold(str(entry.get("caption") or ""))[:CAPTION_PREFIX]
        hit = next((c for c, t in folded if caption and caption in t), None)
        out.append({**entry, "chunk_id": hit})
        if hit is None:
            dropped += 1
        else:
            remapped += 1
    return out, remapped, dropped


async def plan_layout(conn: aiosqlite.Connection, pcid: int) -> LayoutPlan:
    """Decide one paper's layout index (no writes)."""
    async with conn.execute(
        "SELECT layout_json FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"paper_content {pcid} not found")
    if not row[0]:
        return LayoutPlan("no_layout")
    layout = json.loads(row[0])
    if not isinstance(layout, list):
        return LayoutPlan("no_layout")
    async with conn.execute(
        "SELECT id, text FROM chunks WHERE paper_content_id = ? ORDER BY id", (pcid,),
    ) as cur:
        chunks = [(int(r[0]), str(r[1])) for r in await cur.fetchall()]
    entries = [e for e in layout if isinstance(e, dict)]
    new, remapped, dropped = remap_layout(entries, chunks)
    if not remapped and not dropped:
        return LayoutPlan("unchanged")
    return LayoutPlan("updated", remapped, dropped, new)


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-repair-layout",
        description="Re-point stale figure/table layout entries (idempotent).",
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
                plan = await plan_layout(conn, pcid)
                if plan.status == "updated" and not args.dry_run:
                    await conn.execute(
                        "UPDATE paper_content SET layout_json = ? WHERE id = ?",
                        (json.dumps(plan.layout), pcid))
                    await conn.commit()
            except Exception:  # noqa: BLE001 — per-paper recovery; reported below
                _LOG.exception("pcid=%d: error", pcid)
                counts["error"] += 1
                continue
            counts[plan.status] += 1
            if plan.status == "updated":
                _LOG.info("pcid=%d: updated (remapped %d, dropped %d)",
                          pcid, plan.remapped, plan.dropped)
    _LOG.info("summary%s: %s", " (dry run)" if args.dry_run else "",
              dict(sorted(counts.items())))
    return 1 if counts["error"] else 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
