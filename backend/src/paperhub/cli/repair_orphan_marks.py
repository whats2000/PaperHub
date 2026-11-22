"""Soft-delete reading marks that have no place in their paper's note.

A mark is in its paper's note as its reference block or as inline
citations (``markCite`` chips). Before 2026-11-22 taking a mark's last
chip out of the note (Backspace in the note card) left the mark live on
the paper with neither: the reader drew it, the note card had nothing to
show, and nothing could delete it. The op engine now deletes such a mark
with its last chip (``ops._follow_cites``); this step repairs the marks
left behind before that.

An orphan is soft-deleted (``deleted_at``), not put back in the note: the
user took its citation out, which now deletes the mark, and a soft delete
can still be undone (``restore_mark``). Marks of a note in the trash are
left alone (restoring the note brings their blocks back). Idempotent: a
second run finds nothing.

Usage:
    uv run paperhub-repair-orphan-marks --dry-run  # list only
    uv run paperhub-repair-orphan-marks            # soft-delete them
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import has_table
from paperhub.notes.inline_merge import cite_ids


async def repair(db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Counts: ``checked`` (live marks whose paper note is live),
    ``anchored``; ``orphans`` lists each mark with no place in its note
    ({mark_id, paper_id, note_id}), soft-deleted unless ``dry_run``."""
    out: dict[str, Any] = {"checked": 0, "anchored": 0, "orphans": []}
    async with open_db(db_path) as conn:
        if not await has_table(conn, "note_marks"):
            return out
        async with conn.execute(
            "SELECT m.id, m.paper_content_id, m.block_id, n.id "
            "FROM note_marks m JOIN notes n ON n.target_kind = 'paper' "
            "AND n.target_id = CAST(m.paper_content_id AS TEXT) "
            "WHERE m.deleted_at IS NULL AND n.deleted_at IS NULL "
            "ORDER BY m.paper_content_id, m.id",
        ) as cur:
            marks = await cur.fetchall()
        places: dict[int, tuple[set[str], set[str]]] = {}
        for mark_id, paper_id, block_id, note_id in marks:
            if note_id not in places:
                async with conn.execute(
                    "SELECT id, payload_json FROM note_blocks "
                    "WHERE note_id = ? AND deleted_at IS NULL", (note_id,),
                ) as cur:
                    rows = await cur.fetchall()
                cited: set[str] = set()
                for _, payload in rows:
                    cited |= cite_ids(json.loads(payload).get("content"))
                places[note_id] = ({str(r[0]) for r in rows}, cited)
            out["checked"] += 1
            blocks, cited = places[note_id]
            if block_id in blocks or mark_id in cited:
                out["anchored"] += 1
                continue
            out["orphans"].append({"mark_id": mark_id, "paper_id": paper_id,
                                   "note_id": note_id})
        if dry_run or not out["orphans"]:
            return out
        now = datetime.now(UTC).isoformat()
        await conn.executemany(
            "UPDATE note_marks SET deleted_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            [(now, o["mark_id"]) for o in out["orphans"]])
        await conn.commit()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = asyncio.run(repair(load_settings().db_path, dry_run=args.dry_run))
    verb = "would soft-delete" if args.dry_run else "soft-deleted"
    for o in out["orphans"]:
        print(f"{verb}: mark {o['mark_id']} (paper {o['paper_id']}, "
              f"note {o['note_id']}): no block or citation in its note")
    print(f"summary: checked={out['checked']} anchored={out['anchored']} "
          f"{verb}={len(out['orphans'])}"
          + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
