"""Narrow the citations of ideas stored before citation narrowing (QA I5).

New ideas store only the chunks of the cited section that carry the
mechanism (``paperhub.ideation.narrow``). Ideas stored before that cite the
whole section (AlphaGenome's 130-chunk "Methods"). This re-runs the same
narrowing on every stored idea, from the stored section, the document's
current card and the idea's title and rationale. Deterministic and idempotent:
a second run changes nothing. The stored section name is kept; an idea whose
section no longer has chunks is left as it is and reported.

Usage:
    uv run paperhub-narrow-idea-citations            # rewrite in place
    uv run paperhub-narrow-idea-citations --dry-run  # report only
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.ideation.agent import field_text
from paperhub.ideation.cards import cached_card
from paperhub.ideation.models import MechanismCard
from paperhub.ideation.narrow import narrow


def _ids(raw: Any) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


async def _end(
    conn: aiosqlite.Connection, doc: int, section: str, title: str, rationale: str,
    cards: dict[int, MechanismCard | None],
) -> list[int] | None:
    if doc not in cards:
        hit = await cached_card(conn, doc)
        cards[doc] = hit[0] if hit is not None else None
    card = cards[doc]
    words = field_text(card, [section]) if card is not None else ""
    got = await narrow(conn, paper_content_id=doc, sections=[section],
                       query_text=f"{words} {title} {rationale}")
    return got.chunk_ids if got is not None else None


async def migrate(db_path: Path, *, dry_run: bool) -> list[dict[str, Any]]:
    """One row per idea: ``status`` ``narrowed`` (chunk ids changed),
    ``unchanged`` or ``no_chunks`` (a cited section has no chunks any more)."""
    rows: list[dict[str, Any]] = []
    cards: dict[int, MechanismCard | None] = {}
    async with open_db(db_path) as conn:
        async with conn.execute(
            "SELECT id, source_doc, source_section, source_chunk_ids_json, target_doc, "
            "target_section, target_chunk_ids_json, title, rationale FROM ideas ORDER BY id",
        ) as cur:
            ideas = list(await cur.fetchall())
        updates: list[tuple[str, str, int]] = []
        for iid, sdoc, ssec, sraw, tdoc, tsec, traw, title, rationale in ideas:
            before_s, before_t = _ids(sraw), _ids(traw)
            src = await _end(conn, int(sdoc), str(ssec), str(title), str(rationale or ""),
                             cards)
            tgt: list[int] | None = before_t
            if tdoc is not None and tsec:
                tgt = await _end(conn, int(tdoc), str(tsec), str(title),
                                 str(rationale or ""), cards)
            row: dict[str, Any] = {"idea_id": int(iid), "source": [len(before_s)],
                                   "target": [len(before_t)]}
            if src is None or tgt is None:
                row["status"] = "no_chunks"
            else:
                row["source"].append(len(src))
                row["target"].append(len(tgt))
                changed = src != before_s or tgt != before_t
                row["status"] = "narrowed" if changed else "unchanged"
                if changed:
                    updates.append((json.dumps(src), json.dumps(tgt), int(iid)))
            rows.append(row)
        if updates and not dry_run:
            async with write_transaction(conn):
                await conn.executemany(
                    "UPDATE ideas SET source_chunk_ids_json = ?, target_chunk_ids_json = ? "
                    "WHERE id = ?", updates)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rows = asyncio.run(migrate(load_settings().db_path, dry_run=args.dry_run))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        arrows = " ".join("->".join(map(str, r[k])) for k in ("source", "target"))
        print(f"{r['idea_id']:>5} {r['status']:<9} source/target chunks {arrows}")
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no ideas",
          "(dry run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
