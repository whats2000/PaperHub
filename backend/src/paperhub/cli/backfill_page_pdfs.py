"""Give papers ingested before evidence pages their page PDF and place their
LaTeX figures on it.

For each paper without a page PDF: a strict pdflatex compile of its LaTeX
source into ``asset/paper.pdf``, else the arXiv PDF (fetched once — a failure
marker stops retries), else a lenient compile. Then every LaTeX figure
(``env_index`` set) without a place gets ``page`` / ``bbox`` on that PDF.
PDF papers already have one (their own file) and are left alone.

``--verify`` fails (exit 1) for each paper without a page PDF; LaTeX figures
that could not be placed are listed as warnings — their citations still open
the figure in the HTML reader and ``view_figure`` falls back to the image.

Idempotent: a done paper reports ``unchanged``.

Usage:
    uv run paperhub-backfill-page-pdfs --dry-run
    uv run paperhub-backfill-page-pdfs [--paper-content-id 25]
    uv run paperhub-backfill-page-pdfs --verify
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.config import load_settings
from paperhub.pipelines.figure_pages import place_latex_figures
from paperhub.pipelines.page_pdf import compile_paper_pdf, fetch_arxiv_pdf, page_pdf_path
from paperhub.pipelines.paper_asset import read_paper_asset, write_paper_asset

_LOG = logging.getLogger("paperhub.backfill_page_pdfs")


@dataclass
class PagePdfResult:
    # unchanged | compiled | fetched | failed | no_source | would_compile | would_fetch
    status: str
    placed: list[str] = field(default_factory=list)


def _main_tex(source_path: str | None) -> Path | None:
    if not source_path:
        return None
    p = Path(source_path)
    return p if p.suffix.lower() == ".tex" and p.is_file() else None


def _make_pdf(
    main: Path | None, arxiv_id: str | None, cache_dir: Path, *, allow_fetch: bool = True,
) -> str:
    if main is not None and compile_paper_pdf(main, cache_dir) is not None:
        return "compiled"
    if allow_fetch and arxiv_id and fetch_arxiv_pdf(arxiv_id, cache_dir) is not None:
        return "fetched"
    if main is not None and compile_paper_pdf(main, cache_dir, strict=False) is not None:
        return "compiled"
    return "failed"


def _place(cache_dir: Path, *, replace_places: bool) -> list[str]:
    """Place unplaced LaTeX figures, or with ``replace_places`` place all of
    them afresh (after the matcher changed); returns the ids whose place was
    added or changed. The asset is written once, after placing."""
    asset = read_paper_asset(cache_dir)
    if asset is None or not any(f.env_index is not None for f in asset.figures):
        return []
    if replace_places:
        cleared = replace(asset, figures=[
            replace(f, page=None, bbox=None) if f.env_index is not None else f
            for f in asset.figures])
        fresh = place_latex_figures(cache_dir, asset=cleared)
        if fresh is None:
            return []
        old = {f.id: (f.page, f.bbox) for f in asset.figures}
        changed = [f.id for f in fresh.figures
                   if f.env_index is not None and (f.page, f.bbox) != old.get(f.id)]
        if changed:
            write_paper_asset(fresh, cache_dir)
        return changed
    before = {f.id for f in asset.figures if f.env_index is not None and f.page is None}
    if not before:
        return []
    placed = place_latex_figures(cache_dir)
    if placed is None:
        return []
    gained = [f.id for f in placed.figures if f.id in before and f.page is not None]
    if gained:
        write_paper_asset(placed, cache_dir)
    return gained


def process_paper(
    kind: str | None, source_path: str | None, arxiv_id: str | None, cache_dir: Path,
    *, dry_run: bool, replace_places: bool = False, allow_fetch: bool = True,
) -> PagePdfResult:
    del kind  # the page PDF follows from the files, not the kind
    cache_dir = Path(cache_dir)
    main = _main_tex(source_path)
    status = "unchanged"
    if page_pdf_path(cache_dir) is None:
        if main is None and not arxiv_id:
            return PagePdfResult("no_source")
        if dry_run:
            return PagePdfResult("would_compile" if main is not None else "would_fetch")
        status = _make_pdf(main, arxiv_id, cache_dir, allow_fetch=allow_fetch)
        if status == "failed":
            return PagePdfResult(status)
    if dry_run:
        return PagePdfResult(status)
    return PagePdfResult(status, _place(cache_dir, replace_places=replace_places))


def verify_paper(cache_dir: Path) -> list[str]:
    """Problems that fail the check: a paper without a page PDF."""
    return [] if page_pdf_path(Path(cache_dir)) is not None else ["no page PDF"]


def unplaced_figures(cache_dir: Path) -> list[str]:
    """LaTeX figures with no place on the page PDF (warnings only)."""
    asset = read_paper_asset(Path(cache_dir))
    return [f.id for f in (asset.figures if asset else [])
            if f.env_index is not None and f.page is None]


async def _papers(conn: aiosqlite.Connection, only: int | None) -> list[tuple[Any, ...]]:
    sql = "SELECT id, kind, source_path, arxiv_id, source_dir_path FROM paper_content"
    args: tuple[int, ...] = ()
    if only is not None:
        sql += " WHERE id = ?"
        args = (only,)
    async with conn.execute(sql + " ORDER BY id", args) as cur:
        return [tuple(r) for r in await cur.fetchall()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperhub-backfill-page-pdfs",
        description="Give every paper a page PDF and place its LaTeX figures (idempotent).",
    )
    parser.add_argument("--paper-content-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without compiling or writing.")
    parser.add_argument("--no-arxiv", action="store_true",
                        help="Never fetch an arXiv PDF (offline); a failed strict "
                             "compile goes straight to the lenient one.")
    parser.add_argument("--verify", action="store_true",
                        help="Only check that every paper has a page PDF.")
    parser.add_argument("--replace-places", action="store_true",
                        help="Place every LaTeX figure afresh (after the placement "
                             "rules changed), not only the unplaced ones.")
    return parser


async def _amain() -> int:
    args = _parser().parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = load_settings()
    counts: Counter[str] = Counter()
    async with aiosqlite.connect(settings.db_path) as conn:
        for pid, kind, source_path, arxiv_id, sd in await _papers(conn, args.paper_content_id):
            cache_dir = Path(sd)
            try:
                if args.verify:
                    problems = verify_paper(cache_dir)
                    counts["no_page_pdf" if problems else "ok"] += 1
                    if problems:
                        _LOG.warning("pcid=%d: no page PDF", pid)
                    unplaced = unplaced_figures(cache_dir)
                    if unplaced:
                        _LOG.warning("pcid=%d: figures without a page place "
                                     "(fall back to the image): %s", pid, unplaced)
                    continue
                result = await asyncio.to_thread(
                    process_paper, kind, source_path, arxiv_id, cache_dir,
                    dry_run=args.dry_run, allow_fetch=not args.no_arxiv,
                    replace_places=args.replace_places)
            except Exception:  # noqa: BLE001 - per-paper recovery; reported below
                _LOG.exception("pcid=%d: error", pid)
                counts["error"] += 1
                continue
            counts[result.status] += 1
            if result.status != "unchanged" or result.placed:
                _LOG.info("pcid=%d: %s%s", pid, result.status,
                          f" (places set or changed: {result.placed})" if result.placed else "")
    _LOG.info("summary%s: %s", " (dry run)" if args.dry_run else "",
              dict(sorted(counts.items())))
    return 1 if counts["error"] or counts["failed"] or counts["no_page_pdf"] else 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
