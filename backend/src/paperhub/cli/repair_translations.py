"""Repair the bilingual-reading translation cache (H1 QA fixes), idempotently.

Three kinds of cached row are fixed:

* ``echo`` — the model returned the English source instead of a translation
  and it was cached as ``ok`` before the echo check existed (paper 89's
  abstract). Deleted; the next view translates it again.
* ``stale_pdf`` — a PDF paragraph row (``p:<block_id>``) whose key is no longer
  a paragraph, or whose source text changed, after the paragraph rules changed
  (front matter no longer merged; references and bylines skipped). It can never
  be served again (the cache is keyed on the source hash). Deleted.
* ``alias`` — a row under a non-canonical language label ("Japanese" for
  "日本語"). Re-keyed to the canonical label, or deleted when the canonical
  label already has that block.

HTML rows (``h:<n>``) are checked for echoes only: their source text lives in
the browser. Usage:

    uv run python -m paperhub.cli.repair_translations --dry-run
    uv run python -m paperhub.cli.repair_translations [--paper-content-id 89]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from paperhub.config import load_settings
from paperhub.pipelines.paper_asset import read_block_assets
from paperhub.translation.language import canonical_language
from paperhub.translation.pdf_paragraphs import build_paragraphs
from paperhub.translation.placeholders import source_hash
from paperhub.translation.quality import looks_untranslated

_LOG = logging.getLogger("paperhub.repair_translations")


@dataclass(frozen=True)
class Action:
    kind: str  # echo | stale_pdf | alias_rekey | alias_duplicate
    row_id: int
    paper_content_id: int
    block_key: str
    language: str
    new_language: str | None = None


async def _pdf_hashes(conn: aiosqlite.Connection, pcid: int) -> dict[str, str] | None:
    """``{paragraph key: source hash}`` for a PDF paper; ``None`` without blocks."""
    async with conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if not row or not row[0]:
        return None
    got = await asyncio.to_thread(read_block_assets, Path(row[0]))
    if got is None:
        return None
    return {p.key: source_hash(p.text) for p in build_paragraphs(got[0])}


async def plan(conn: aiosqlite.Connection, paper_content_id: int | None = None) -> list[Action]:
    """Every repair the cache needs (no writes)."""
    sql = ("SELECT id, paper_content_id, block_key, language, source_hash, text"
           " FROM translations")
    params: tuple[int, ...] = ()
    if paper_content_id is not None:
        sql += " WHERE paper_content_id = ?"
        params = (paper_content_id,)
    async with conn.execute(sql + " ORDER BY id", params) as cur:
        rows = await cur.fetchall()

    actions: list[Action] = []
    hashes: dict[int, dict[str, str] | None] = {}
    # (paper, key, canonical language) already present under the canonical label.
    canonical_taken = {
        (int(r[1]), str(r[2]), str(r[3])) for r in rows
        if canonical_language(str(r[3])) == str(r[3])
    }
    for rid, pcid, key, language, shash, text in rows:
        rid, pcid, key, language = int(rid), int(pcid), str(key), str(language)
        if looks_untranslated(str(text), language):
            actions.append(Action("echo", rid, pcid, key, language))
            continue
        if key.startswith("p:"):
            if pcid not in hashes:
                hashes[pcid] = await _pdf_hashes(conn, pcid)
            current = hashes[pcid]
            if current is not None and current.get(key) != shash:
                actions.append(Action("stale_pdf", rid, pcid, key, language))
                continue
        canonical = canonical_language(language)
        if canonical != language:
            slot = (pcid, key, canonical)
            if slot in canonical_taken:
                actions.append(Action("alias_duplicate", rid, pcid, key, language))
            else:
                canonical_taken.add(slot)
                actions.append(Action("alias_rekey", rid, pcid, key, language, canonical))
    return actions


async def apply(conn: aiosqlite.Connection, actions: list[Action]) -> None:
    """Apply ``actions`` in one transaction."""
    try:
        for a in actions:
            if a.kind == "alias_rekey":
                await conn.execute("UPDATE translations SET language = ? WHERE id = ?",
                                   (a.new_language, a.row_id))
            else:
                await conn.execute("DELETE FROM translations WHERE id = ?", (a.row_id,))
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-repair-translations",
        description="Purge echoed / stale translation rows and merge language aliases.",
    )
    parser.add_argument("--paper-content-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path, timeout=30) as conn:
        actions = await plan(conn, args.paper_content_id)
        for a in actions:
            _LOG.info("%s: paper=%d key=%s language=%s%s", a.kind, a.paper_content_id,
                      a.block_key, a.language,
                      f" -> {a.new_language}" if a.new_language else "")
        if actions and not args.dry_run:
            await apply(conn, actions)
        async with conn.execute("SELECT COUNT(*) FROM translations") as cur:
            total = (await cur.fetchone() or (0,))[0]
    counts = Counter(a.kind for a in actions)
    _LOG.info("summary%s: %s (rows now: %s)", " (dry run)" if args.dry_run else "",
              dict(sorted(counts.items())) or "nothing to repair", total)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
