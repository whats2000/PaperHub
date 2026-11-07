"""Build the H3 mechanism card for every document in the library.

Ideation reads one card per document; ``generate_ideas`` builds missing cards
lazily, but documents ingested before H3 (or whose card build failed) should
be upgraded by the operator path too (``scripts/reingest_all_papers.ps1``).
Idempotent: a card cached for the current digest, card prompt and card model
is left alone. Sequential, one document at a time (a digest build fans out one
LLM call per section). Each build is traced on a run of the ideation session.

Usage:
    uv run paperhub-backfill-cards              # build missing / stale cards
    uv run paperhub-backfill-cards --dry-run    # report only (no LLM calls)
    uv run paperhub-backfill-cards --ids 88     # only these documents

Exit code 1 when any document fails, so a failure is never a footnote.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any

from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.ideation.agent import card_sections
from paperhub.ideation.cards import CARD_SLOT, build_card, cached_card
from paperhub.ideation.internal_session import ideation_session_id
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer

_LOG = logging.getLogger("paperhub.backfill_cards")

BACKFILL_RUN_JSON = '{"ideation_mode": "backfill_cards"}'


async def backfill(
    db_path: Path, *, adapter: LlmAdapter, card_model: str, dry_run: bool,
    ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """One row per document (only ``ids`` when given): ``status`` is ``ready``
    (cached and current), ``built`` (new or changed card), ``error`` (with
    ``reason``) — or, in a dry run, ``ready``, ``stale`` (cached for another
    card prompt/model), ``uncitable`` (the card cites no section, e.g. a
    sectionless document cached before it could cite "Full text", which a run
    upgrades without an LLM call) or ``missing``. A dry run does not load
    digests, so a card whose digest changed shows as ready."""
    rows: list[dict[str, Any]] = []
    async with open_db(db_path) as conn:
        async with conn.execute("SELECT id, title FROM paper_content ORDER BY id") as cur:
            docs = [(int(r[0]), str(r[1] or "")) for r in await cur.fetchall()]
        if ids is not None:
            docs = [d for d in docs if d[0] in set(ids)]
        tracer: Tracer | None = None
        if not dry_run and docs:
            session_id = await ideation_session_id(conn)
            async with write_transaction(conn):
                ins = await conn.execute(
                    "INSERT INTO runs (session_id, status, routing_decision_json) "
                    "VALUES (?, 'running', ?)", (session_id, BACKFILL_RUN_JSON))
                assert ins.lastrowid is not None
                run_id = int(ins.lastrowid)
            tracer = Tracer(conn, run_id=run_id, branch="")
        for pid, title in docs:
            before = await cached_card(conn, pid)
            row: dict[str, Any] = {"paper_content_id": pid, "title": title}
            if tracer is None:
                if before is None:
                    row["status"] = "missing"
                elif before[1][1:] != (CARD_SLOT, card_model):
                    row["status"] = "stale"
                elif not card_sections(before[0]):
                    row["status"] = "uncitable"
                else:
                    row["status"] = "ready"
            else:
                try:
                    await build_card(conn, pid, adapter=adapter, model=card_model,
                                     tracer=tracer)
                except Exception as exc:  # noqa: BLE001 — reported per document
                    row.update(status="error", reason=f"{type(exc).__name__}: {exc}")
                else:
                    after = await cached_card(conn, pid)
                    same = before is not None and after is not None and before == after
                    row["status"] = "ready" if same else "built"
            rows.append(row)
        if tracer is not None:
            failed = any(r["status"] == "error" for r in rows)
            async with write_transaction(conn):
                await conn.execute(
                    "UPDATE runs SET status = ?, finished_at = datetime('now') WHERE id = ?",
                    ("error" if failed else "ok", tracer.run_id))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ids", type=lambda v: [int(x) for x in v.split(",") if x.strip()],
                    help="comma-separated paper_content ids (default: every document)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    db_path = settings.db_path

    async def _run() -> list[dict[str, Any]]:
        from paperhub.app import apply_settings_overlay_at_boot
        from paperhub.llm.litellm_adapter import LiteLlmAdapter

        async with open_db(db_path) as conn:
            await apply_settings_overlay_at_boot(conn)
        model = load_settings().ideation_card_model  # after the DB overlay
        return await backfill(db_path, adapter=LiteLlmAdapter(), card_model=model,
                              dry_run=args.dry_run, ids=args.ids)

    rows = asyncio.run(_run())
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        line = f"{r['paper_content_id']:>5} {r['status']:<8} {r['title'][:70]}"
        if "reason" in r:
            line += f"  — {r['reason'][:200]}"
        print(line)
    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
