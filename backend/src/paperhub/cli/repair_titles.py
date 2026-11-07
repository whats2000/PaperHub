"""Repair PDF-upload titles that came from a bad page-1 pick, in place.

Before the page-1 title fix, a PDF without an embedded title took the
largest-font text on page 1 as its title, so a journal wordmark ("applied
sciences") or a drop cap could become the title. This step re-derives each
PDF upload's title with the current rules and replaces the stored one only
when the stored title was produced automatically: it equals the old
derivation (metadata title, old largest-font pick) or the filename-stem
fallback. A title typed in the upload dialog, or one an LLM extracted, never
matches those and is kept.

The new title is carried to ``paper_content.title`` (the FTS index follows
through its update trigger) and the cached ``digest.json``; the mechanism
card's ``digest_hash`` is resynced so the card stays a cache hit. Every paper
is also checked for title defects (``garbled`` U+FFFD / control characters,
``not_title_shaped`` wordmark-like text, ``filename_stem``); a defect the
step cannot fix is reported as ``flagged``.

Idempotent: a repaired paper reports ``unchanged``.

Usage:
    uv run paperhub-repair-titles --dry-run
    uv run paperhub-repair-titles [--paper-content-id 89]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite
import pymupdf

from paperhub.config import load_settings
from paperhub.pipelines.extract import (
    _GARBLED_CHAR,
    _extract_pdf_metadata,
    _sanitise_pdf_title,
    legacy_page1_title,
    looks_like_title_text,
)

_LOG = logging.getLogger("paperhub.repair_titles")


@dataclass
class TitlePlan:
    # updated | unchanged | kept_typed_title | flagged
    status: str
    old: str
    new: str | None = None
    defects: list[str] = field(default_factory=list)


def title_defects(title: str, source_path: str) -> list[str]:
    """Defect classes a stored title shows on its own."""
    if _GARBLED_CHAR.search(title):
        return ["garbled"]
    if source_path and title == Path(source_path).stem:
        return ["filename_stem"]
    if not looks_like_title_text(title):
        return ["not_title_shaped"]
    return []


def _auto_titles(pdf: Path) -> tuple[str, set[str]]:
    """(current-rule title, every title the old ingest could have produced)."""
    current = str(_extract_pdf_metadata(pdf)["title"])
    with pymupdf.open(pdf) as doc:  # type: ignore[no-untyped-call]
        meta = (doc.metadata or {}).get("title")
        legacy = {legacy_page1_title(doc), pdf.stem}
    if isinstance(meta, str) and meta.strip():
        legacy.add(meta.strip())
        legacy.add(_sanitise_pdf_title(meta))
    legacy.discard("")
    return current, legacy


async def plan_title(conn: aiosqlite.Connection, pcid: int) -> TitlePlan:
    """Decide one paper's title (no writes)."""
    async with conn.execute(
        "SELECT kind, title, source_path FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"paper_content {pcid} not found")
    kind, title, source_path = str(row[0]), str(row[1]), str(row[2] or "")
    defects = title_defects(title, source_path if kind == "pdf_upload" else "")
    pdf = Path(source_path)
    if kind != "pdf_upload" or pdf.suffix.lower() != ".pdf" or not pdf.is_file():  # noqa: ASYNC240
        return TitlePlan("flagged" if defects else "unchanged", title, defects=defects)
    current, legacy = _auto_titles(pdf)
    if not current or current == title:
        return TitlePlan("flagged" if defects else "unchanged", title, defects=defects)
    if title not in legacy:
        return TitlePlan("kept_typed_title", title, defects=defects)
    return TitlePlan("updated", title, current, defects)


async def apply_title(conn: aiosqlite.Connection, pcid: int, new: str) -> None:
    """Write the title and carry it to digest.json + the mechanism card hash."""
    from paperhub.ideation.cards import digest_hash  # local: avoid an import cycle
    from paperhub.models.slide_domain import PaperDigest

    await conn.execute("UPDATE paper_content SET title = ? WHERE id = ?", (new, pcid))
    async with conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    path = Path(str(row[0])) / "digest.json" if row and row[0] else None
    if path is not None and path.is_file():
        try:
            old = PaperDigest.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            old = None
            _LOG.warning("pcid=%d: digest.json unreadable; left for a rebuild", pcid)
        if old is not None and old.title != new:
            renamed = old.model_copy(update={"title": new})
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(renamed.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(path)
            # The card was built from this digest: keep it a cache hit.
            await conn.execute(
                "UPDATE mechanism_cards SET digest_hash = ? "
                "WHERE paper_content_id = ? AND digest_hash = ?",
                (digest_hash(renamed), pcid, digest_hash(old)),
            )
    await conn.commit()


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-repair-titles",
        description="Re-derive auto-picked PDF titles in place (idempotent).",
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
                plan = await plan_title(conn, pcid)
                if plan.status == "updated" and plan.new and not args.dry_run:
                    await apply_title(conn, pcid, plan.new)
            except Exception:  # noqa: BLE001 — per-paper recovery; reported below
                _LOG.exception("pcid=%d: error", pcid)
                counts["error"] += 1
                continue
            counts[plan.status] += 1
            extra = f" {plan.old!r} -> {plan.new!r}" if plan.new else ""
            defects = f" defects={plan.defects}" if plan.defects else ""
            _LOG.info("pcid=%d: %s%s%s", pcid, plan.status, extra, defects)
    _LOG.info("summary%s: %s", " (dry run)" if args.dry_run else "", dict(sorted(counts.items())))
    return 1 if counts["error"] else 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
