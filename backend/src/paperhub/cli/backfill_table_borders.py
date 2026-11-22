"""Give every existing table its own lines (Plan H5b.1).

A table's lines live in its ``borders`` prop; a table without one draws
as the Paper preset (a thick top and end rule, a thin rule under the
header). This writes exactly that look into every live table of a live
note that has no lines yet (a trashed note's tables, history and
suggestions keep drawing as Paper), one ``system`` change set per note
(undoable like any change). A table that already has lines is left
alone; a table whose stored content is not a valid grid is reported and
skipped. Idempotent: a second run sets nothing.

Usage:
    uv run paperhub-backfill-table-borders --dry-run  # counts only
    uv run paperhub-backfill-table-borders            # write
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.notes.models import header_depth, table_grid
from paperhub.notes.ops import LIVE_NOTE_SQL, BlockIn, Op, apply_ops
from paperhub.notes.store import get_note
from paperhub.notes.table_borders import dump_borders, parse_borders, preset_lines


async def backfill(db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Counts: ``checked`` (live tables of live notes), ``set`` (would be,
    on a dry run; else the tables the change applied), ``already`` (with
    lines); ``rejected`` names each table skipped or refused
    ({note_id, block_id, reason})."""
    counts: dict[str, Any] = {"checked": 0, "set": 0, "already": 0,
                              "rejected": []}
    async with open_db(db_path) as conn:
        async with conn.execute(
            "SELECT id, note_id, payload_json FROM note_blocks "
            "WHERE deleted_at IS NULL AND type = 'table' "
            f"AND {LIVE_NOTE_SQL} ORDER BY note_id, id",  # noqa: S608
        ) as cur:
            rows = await cur.fetchall()
        by_note: dict[int, list[Op]] = {}
        for bid, note_id, payload in rows:
            counts["checked"] += 1
            try:
                data = json.loads(payload)
                props = dict(data.get("props") or {})
                content = data.get("content")
                if not isinstance(content, dict):
                    raise ValueError("its content is not a table")
                if parse_borders(props.get("borders")) is not None:
                    counts["already"] += 1
                    continue
                _, n, width = table_grid(content)
                lines = preset_lines(n, width, header_depth(content), "paper")
                op = Op(op="update", block_id=bid, block=BlockIn(
                    type="table", props={**props, "borders": dump_borders(lines)},
                    content=content))
            except ValueError as exc:  # includes pydantic's ValidationError
                counts["rejected"].append(
                    {"note_id": note_id, "block_id": bid, "reason": str(exc)[:300]})
                continue
            if dry_run:
                counts["set"] += 1
            by_note.setdefault(note_id, []).append(op)
        if dry_run:
            return counts
        for note_id, ops in by_note.items():
            note = await get_note(conn, note_id)
            if note is None:
                continue
            res = await apply_ops(conn, note_id, base_revision=note.revision,
                                  ops=ops, actor="system",
                                  summary="Give tables their own lines")
            counts["set"] += sum(1 for o in ops if o.block_id in res.applied)
            counts["rejected"] += [
                {"note_id": note_id, "block_id": r["block_id"],
                 "reason": r["reason"]} for r in res.rejected]
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = asyncio.run(backfill(load_settings().db_path, dry_run=args.dry_run))
    for r in out["rejected"]:
        print(f"rejected: note {r['note_id']} block {r['block_id']}: "
              f"{r['reason']}")
    verb = "would set" if args.dry_run else "set"
    print(f"summary: checked={out['checked']} already={out['already']} "
          f"{verb}={out['set']} rejected={len(out['rejected'])}"
          + (" (dry run)" if args.dry_run else ""))
    # A refused table fails the step so the upgrade script reports it.
    return 1 if out["rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
