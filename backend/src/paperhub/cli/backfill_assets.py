"""Backfill the unified PaperAsset onto papers already in the cache (F2 migration).

F2 added the file-based ``PaperAsset`` bundle (figures+captions, equations→LaTeX,
structured sections under ``<cache>/asset/``), but only the *ingest* path writes
it. Every paper ingested before F2 has no ``asset/`` dir, so the F3 slide agent's
``read_paper_asset`` would return ``None`` for them. This CLI replays the same
asset-extraction the pipeline does at ingest, per paper_content row:

  * ``arxiv`` / ``latex_upload`` → ``latex_source_to_asset`` (CPU-only, fast).
  * ``pdf_upload``              → Marker (``marker_doc_to_asset``), the GPU path.

It touches ONLY the filesystem (writes ``asset/``); chunks, embeddings, and the
DB are left untouched (use ``paperhub-reingest`` for those). Idempotent: a paper
that already has ``asset/figures.json`` is skipped unless ``--force``.

CRITICAL — strictly sequential: papers are processed one at a time, and within a
PDF the Marker client batches pages sequentially (``PAPERHUB_MARKER_MAX_PAGES``).
Firing concurrent Marker conversions would exhaust GPU VRAM, forcing CUDA into
the shared-system-memory fallback — catastrophically slow. Do NOT parallelize.

Usage:
    uv run paperhub-backfill-assets                  # all paper_content rows
    uv run paperhub-backfill-assets --paper-content-id 22
    uv run paperhub-backfill-assets --dry-run        # preview, no writes
    uv run paperhub-backfill-assets --force          # rebuild even if asset/ exists
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from paperhub.config import load_settings
from paperhub.pipelines.extract import extract_latex
from paperhub.pipelines.latex_to_asset import latex_source_to_asset
from paperhub.pipelines.marker_client import MarkerClient, get_marker_client
from paperhub.pipelines.marker_to_asset import marker_doc_to_asset
from paperhub.pipelines.paper_asset import read_paper_asset, write_paper_asset

_LOG = logging.getLogger("paperhub.backfill_assets")


@dataclass
class BackfillResult:
    """Outcome of backfilling one paper. ``status`` is one of:
    written / skipped_exists / skipped_no_source / skipped_unknown_kind / dry_run.
    """

    status: str
    figures: int = 0
    equations: int = 0
    sections: int = 0


def build_asset_for_paper(
    *,
    kind: str | None,
    source_path: str | None,
    source_dir_path: str | None,
    title: str,
    marker_client: MarkerClient | None,
    max_pages: int | None,
    force: bool,
    dry_run: bool,
) -> BackfillResult:
    """Build + write the PaperAsset for one paper_content row.

    ``marker_client`` is injected (the live run passes ``get_marker_client()``;
    tests pass a MockTransport-backed client). Only the ``pdf_upload`` branch
    uses it, so the LaTeX path tolerates ``None``.
    """
    if source_dir_path is None:
        return BackfillResult("skipped_no_source")
    cache_dir = Path(source_dir_path)

    # Idempotency: figures.json present → already backfilled (an empty figure
    # list still writes the file, so a figure-less paper is correctly "done").
    if not force and read_paper_asset(cache_dir) is not None:
        return BackfillResult("skipped_exists")

    if kind not in ("arxiv", "latex_upload", "pdf_upload"):
        return BackfillResult("skipped_unknown_kind")
    if source_path is None:
        return BackfillResult("skipped_no_source")

    # Route by the ACTUAL source file, not just `kind`: an arxiv paper whose
    # LaTeX e-print was unrecoverable falls back to a PDF (source.pdf) at
    # ingest, so a kind="arxiv" row can still be a PDF source → Marker path.
    is_pdf_source = source_path.lower().endswith(".pdf")

    if kind == "pdf_upload" or is_pdf_source:
        pdf_path = Path(source_path)
        if not pdf_path.exists():  # e.g. an arxiv-via-pdf row whose source was never persisted
            return BackfillResult("skipped_no_source")
        if dry_run:
            return BackfillResult("dry_run")
        client = marker_client if marker_client is not None else get_marker_client()
        doc = client.extract(pdf_path.read_bytes(), max_pages=max_pages)
        asset = marker_doc_to_asset(doc, source_dir=cache_dir)
    else:
        # LaTeX path. The source dir (where figure files live) is the directory
        # CONTAINING the main .tex — robust across cache layout + flat fixtures,
        # matching paperhub-reingest's resolution.
        latex_source_dir = Path(source_path).parent
        if not latex_source_dir.is_dir():
            return BackfillResult("skipped_no_source")
        try:
            full_text = extract_latex(latex_source_dir).flattened_text
        except FileNotFoundError:
            # Source dir present but empty / no main .tex (unrecoverable source).
            return BackfillResult("skipped_no_source")
        if dry_run:
            return BackfillResult("dry_run")
        asset = latex_source_to_asset(latex_source_dir, full_text, source_dir=cache_dir)

    write_paper_asset(asset, cache_dir)
    return BackfillResult(
        "written",
        figures=len(asset.figures),
        equations=len(asset.equations),
        sections=len(asset.sections),
    )


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-backfill-assets",
        description=(
            "Backfill the unified PaperAsset (figures/equations/sections) onto "
            "papers already in the cache. Marker for pdf_upload, LaTeX-source "
            "for arxiv/latex_upload. Filesystem-only; idempotent; SEQUENTIAL."
        ),
    )
    parser.add_argument("--paper-content-id", type=int, default=None,
                        help="Backfill just this paper_content.id (default: all).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without writing asset/.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even if asset/ already exists.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    settings = load_settings()
    max_pages = settings.marker_max_pages
    # One shared client; reused across papers. Each .extract() is a blocking,
    # sequential call — never overlapped — so the GPU sees one conversion at a time.
    marker_client = get_marker_client()

    async with aiosqlite.connect(settings.db_path) as conn:
        if args.paper_content_id is not None:
            ids = [args.paper_content_id]
        else:
            async with conn.execute(
                "SELECT id FROM paper_content ORDER BY id"
            ) as cur:
                ids = [int(r[0]) for r in await cur.fetchall()]
        _LOG.info("backfilling assets for %d paper(s)...", len(ids))

        counts: dict[str, int] = {}
        for pcid in ids:
            async with conn.execute(
                "SELECT kind, source_path, source_dir_path, title "
                "FROM paper_content WHERE id = ?",
                (pcid,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                _LOG.warning("pcid=%d: paper_content row missing — skipped", pcid)
                counts["missing"] = counts.get("missing", 0) + 1
                continue
            kind, source_path, source_dir_path, title = (
                row[0], row[1], row[2], str(row[3] or ""),
            )
            try:
                # Sync asset build off the event loop (Marker call + file IO).
                res = await asyncio.to_thread(
                    build_asset_for_paper,
                    kind=kind,
                    source_path=source_path,
                    source_dir_path=source_dir_path,
                    title=title,
                    marker_client=marker_client,
                    max_pages=max_pages,
                    force=args.force,
                    dry_run=args.dry_run,
                )
            except Exception as exc:  # noqa: BLE001 — per-paper recovery (e.g. OOM)
                _LOG.exception("pcid=%d (%s) failed: %s", pcid, kind, exc)
                counts["error"] = counts.get("error", 0) + 1
                continue
            counts[res.status] = counts.get(res.status, 0) + 1
            _LOG.info(
                "pcid=%d (%s): %s [figs=%d eqs=%d secs=%d] %s",
                pcid, kind, res.status, res.figures, res.equations, res.sections,
                title[:40],
            )

        _LOG.info("done. summary: %s", dict(sorted(counts.items())))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
