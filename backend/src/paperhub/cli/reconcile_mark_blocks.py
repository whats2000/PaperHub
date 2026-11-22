"""Redraw mark blocks from their mark rows (Plan H5b.1, Task 16).

A mark's colour and style live on its ``note_marks`` row; its note blocks
draw them. Before Task 16 an agent's recolour of a mark block changed the
block only, so a block can disagree with its row. The row is the source of
truth: every live mark block whose ``color`` / ``style`` differ from its
row's is redrawn, one ``system`` change set per note (undoable like any
change). Blocks naming no mark row are left alone. Idempotent: a second run
redraws nothing.

Usage:
    uv run paperhub-reconcile-mark-blocks --dry-run  # counts only
    uv run paperhub-reconcile-mark-blocks            # redraw
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import has_table
from paperhub.notes.inline_merge import MARK_BLOCK_TYPES
from paperhub.notes.ops import BlockIn, Op, apply_ops, drawn_props
from paperhub.notes.store import get_note


async def reconcile(db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Counts: ``checked`` (live mark blocks with a mark row), ``matching``,
    ``redrawn`` (would be, on a dry run; else the blocks the change
    applied) and ``no_mark``; ``rejected`` names each redraw the op engine
    refused ({note_id, block_id, reason})."""
    counts: dict[str, Any] = {"checked": 0, "matching": 0, "redrawn": 0,
                              "no_mark": 0, "rejected": []}
    async with open_db(db_path) as conn:
        if not await has_table(conn, "note_marks"):
            return counts
        async with conn.execute(
            "SELECT id, color, style FROM note_marks") as cur:
            marks = {r[0]: {"color": r[1], "style": r[2]}
                     for r in await cur.fetchall()}
        types = sorted(MARK_BLOCK_TYPES)
        async with conn.execute(
            "SELECT id, note_id, type, payload_json FROM note_blocks "
            f"WHERE deleted_at IS NULL AND type IN ({','.join('?' for _ in types)}) "  # noqa: S608
            "ORDER BY note_id, id", types,
        ) as cur:
            rows = await cur.fetchall()
        by_note: dict[int, list[Op]] = {}
        for bid, note_id, type_, payload in rows:
            data = json.loads(payload)
            props = data.get("props") or {}
            mark = marks.get(props.get("markId"))
            if mark is None:
                counts["no_mark"] += 1
                continue
            counts["checked"] += 1
            want = drawn_props(props, mark)
            if want == props:
                counts["matching"] += 1
                continue
            if dry_run:
                counts["redrawn"] += 1
            by_note.setdefault(note_id, []).append(Op(
                op="update", block_id=bid, block=BlockIn(
                    type=type_, props=want, content=data.get("content"))))
        if dry_run:
            return counts
        for note_id, ops in by_note.items():
            note = await get_note(conn, note_id)
            if note is None:
                continue
            res = await apply_ops(conn, note_id, base_revision=note.revision,
                                  ops=ops, actor="system",
                                  summary="Redraw marks from their rows")
            counts["redrawn"] += sum(
                1 for o in ops if o.block_id in res.applied)
            counts["rejected"] += [
                {"note_id": note_id, "block_id": r["block_id"],
                 "reason": r["reason"]} for r in res.rejected]
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = asyncio.run(reconcile(load_settings().db_path, dry_run=args.dry_run))
    for r in out["rejected"]:
        print(f"rejected: note {r['note_id']} block {r['block_id']}: "
              f"{r['reason']}")
    verb = "would redraw" if args.dry_run else "redrawn"
    print(f"summary: checked={out['checked']} already_matching={out['matching']} "
          f"{verb}={out['redrawn']} no_mark_row={out['no_mark']} "
          f"rejected={len(out['rejected'])}"
          + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
