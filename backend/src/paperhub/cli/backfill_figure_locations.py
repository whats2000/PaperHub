"""Record where each figure sits in the reader, for papers ingested before
figure citations, and check that every one can be opened.

A ``[fig:P/ID]`` citation opens the Citation Canvas on the figure. The asset
now records that place at ingest; this step adds it to older assets:

* HTML reader (LaTeX papers): each figure's ``env_index`` (which figure
  environment it is), so ``paperhub-rerender-html`` puts a ``phfig-NNN``
  anchor before it. Figures commented out in the source are dropped (they
  are not in the paper); every other id stays as it is.
* PDF reader, Marker asset: each figure's ``bbox`` from the persisted Marker
  blocks (``blocks.json``), the N-th Figure/Picture block of a page being the
  page's N-th figure; on a page with more picture blocks than figures, the
  blocks whose shape matches the images are taken.
* PDF reader, PyMuPDF asset: each figure's ``bbox`` from the PDF, replaying
  the extraction order.

``--verify`` (run after the HTML re-render) reports, per paper, any figure the
reader cannot open on: no ``bbox`` (PDF) or no ``phfig`` anchor followed by its
``<figure>`` (HTML). Idempotent; a paper already done reports ``unchanged``.

Usage:
    uv run paperhub-backfill-figure-locations --dry-run
    uv run paperhub-backfill-figure-locations [--paper-content-id 25]
    uv run paperhub-backfill-figure-locations --verify
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import aiosqlite
import pymupdf
from PIL import Image

from paperhub.config import load_settings
from paperhub.pipelines.figure_match import locate_crop
from paperhub.pipelines.figure_merge import Merge, apply_merges, plan_merges
from paperhub.pipelines.latex_to_asset import PanelSource, figure_envs, stage_panels
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    paper_asset_dir,
    read_block_assets,
    read_paper_asset,
    write_paper_asset,
)
from paperhub.pipelines.pymupdf_to_asset import image_bbox
from paperhub.pipelines.sentinels import figure_dom_id

_LOG = logging.getLogger("paperhub.backfill_figure_locations")
_PICTURE_TYPES = ("Figure", "Picture")
# A picture block matches an image when their width/height ratios differ by
# less than this factor.
MAX_ASPECT_RATIO_GAP = 1.15


@dataclass
class LocationPlan:
    # updated | unchanged | no_asset | mismatch
    status: str
    asset: PaperAsset | None = None
    dropped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # Multi-panel LaTeX figures whose image is only their first panel: the
    # composite of all panels is written when the plan is applied.
    recompose: dict[str, list[PanelSource]] = field(default_factory=dict)
    # PDF figures Marker split into pieces, joined when the plan is applied.
    merges: list[Merge] = field(default_factory=list)


def apply_recompose(plan: LocationPlan, cache_dir: Path) -> None:
    """Write each planned composite image and point its figure at it."""
    if plan.asset is None or not plan.recompose:
        return
    figs = []
    for f in plan.asset.figures:
        panels = plan.recompose.get(f.id)
        if panels:
            rel = stage_panels(panels, paper_asset_dir(cache_dir) / "figures", int(f.id[4:]))
            f = replace(f, image_path=rel, panels=len(panels))
        figs.append(f)
    plan.asset = replace(plan.asset, figures=figs)


def reader_mode(cache_dir: Path) -> str:
    """The Citation Canvas shows the PDF when one sits in the cache root."""
    return "pdf" if any(cache_dir.glob("*.pdf")) else "html"


def plan_latex(
    asset: PaperAsset, latex_source_dir: Path, flattened: str,
) -> LocationPlan:
    """``env_index`` for each figure; commented-out figures dropped. Stored
    figures are matched to figure environments in document order by caption
    (older ingests numbered some environments differently, so the stored id
    is kept, never re-derived); a figure with no matching environment after
    the previous one is a mismatch, so no figure is given another's place."""
    envs = figure_envs(latex_source_dir, flattened)
    kept: list[FigureAsset] = []
    dropped: list[str] = []
    problems: list[str] = []
    recompose: dict[str, list[PanelSource]] = {}
    at = 0
    for f in asset.figures:
        if f.env_index is not None:
            # Placed by an earlier run (or a current ingest): keep that place.
            # Matching by caption again could take a commented-out draft
            # with the same caption (paper 62).
            hit = next((i for i, e in enumerate(envs)
                        if e.env_index == f.env_index
                        and f.caption in (e.caption, e.main_caption)), None)
        else:
            hit = next((i for i in range(at, len(envs))
                        if f.caption in (envs[i].caption, envs[i].main_caption)), None)
        if hit is None:
            problems.append(f"{f.id}: no figure environment with its caption")
            continue
        at = hit + 1
        if envs[hit].commented:
            dropped.append(f.id)
            continue
        # The caption the paper prints (older assets stored a panel's caption
        # or a commented draft's, which cannot be found on the page).
        kept.append(replace(f, env_index=envs[hit].env_index,
                            caption=envs[hit].main_caption or f.caption))
        panels = envs[hit].panels
        if len(panels) > 1 and f.panels != len(panels):
            recompose[f.id] = panels
    if problems:
        return LocationPlan("mismatch", problems=problems)
    new = replace(asset, figures=kept)
    status = "unchanged" if new == asset and not recompose else "updated"
    return LocationPlan(status, new, dropped, recompose=recompose)


def _aspect(w: float, h: float) -> float:
    return w / h if h > 0 else 0.0


def _match_page(
    figs: list[FigureAsset], blocks: list[tuple[float, float, float, float]],
    aspects: list[float],
) -> list[tuple[float, float, float, float]] | None:
    """The blocks (kept in reading order) whose shapes best match the figures'
    images, or None when no choice matches every figure."""
    best: tuple[float, tuple[int, ...]] | None = None
    for pick in itertools.combinations(range(len(blocks)), len(figs)):
        gaps = []
        for fi, bi in enumerate(pick):
            b = blocks[bi]
            box = _aspect(b[2] - b[0], b[3] - b[1])
            if box <= 0 or aspects[fi] <= 0:
                gaps.append(math.inf)
            else:
                gaps.append(abs(math.log(box / aspects[fi])))
        if max(gaps) > math.log(MAX_ASPECT_RATIO_GAP):
            continue
        if best is None or sum(gaps) < best[0]:
            best = (sum(gaps), pick)
    return [blocks[i] for i in best[1]] if best else None


def plan_marker(asset: PaperAsset, cache_dir: Path) -> LocationPlan:
    """``bbox`` for each figure from the persisted Marker blocks."""
    if all(f.bbox is not None for f in asset.figures):
        return LocationPlan("unchanged", asset)
    loaded = read_block_assets(cache_dir)
    if loaded is None:
        return LocationPlan("mismatch", problems=["no blocks.json"])
    by_page: dict[int | None, list[tuple[float, float, float, float]]] = defaultdict(list)
    for b in loaded[0]:
        if b.block_type in _PICTURE_TYPES:
            by_page[b.page].append(b.bbox)
    figs_by_page: dict[int | None, list[FigureAsset]] = defaultdict(list)
    for f in asset.figures:
        figs_by_page[f.page].append(f)
    boxes: dict[str, tuple[float, float, float, float]] = {}
    problems: list[str] = []
    for page, figs in figs_by_page.items():
        blocks = by_page.get(page, [])
        if len(blocks) == len(figs):
            boxes.update({f.id: b for f, b in zip(figs, blocks, strict=True)})
            continue
        aspects = []
        for f in figs:
            with Image.open(f.abs_image_path(cache_dir)) as im:
                aspects.append(_aspect(*im.size))
        picked = _match_page(figs, blocks, aspects) if len(blocks) > len(figs) else None
        if picked is not None:
            boxes.update({f.id: b for f, b in zip(figs, picked, strict=True)})
            continue
        # The blocks cannot tell: find each crop on its rendered page.
        pdfs = sorted(cache_dir.glob("*.pdf"))
        for f in figs:
            found = (locate_crop(pdfs[0], f.page, f.abs_image_path(cache_dir))
                     if pdfs and f.page is not None else None)
            if found is None:
                problems.append(f"{f.id}: not found on page {f.page} "
                                f"({len(figs)} figures, {len(blocks)} picture blocks)")
            else:
                boxes[f.id] = found
    if problems:
        return LocationPlan("mismatch", problems=problems)
    new = replace(asset, figures=[replace(f, bbox=boxes[f.id]) for f in asset.figures])
    return LocationPlan("updated", new)


def plan_pymupdf(asset: PaperAsset, pdf_path: Path) -> LocationPlan:
    """``bbox`` for each figure, replaying ``pymupdf_to_asset``'s order."""
    if all(f.bbox is not None for f in asset.figures):
        return LocationPlan("unchanged", asset)
    found: list[tuple[int, tuple[float, float, float, float] | None]] = []
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        for page_index in range(doc.page_count):
            page = doc[page_index]
            for img in page.get_images(full=True):
                try:
                    data = doc.extract_image(img[0])["image"]
                except Exception:  # noqa: BLE001 - mirrors the extractor
                    continue
                if data:
                    found.append((page_index, image_bbox(page, img[0])))
    by_id = {f"fig-{n:03d}": hit for n, hit in enumerate(found)}
    problems = [f"{f.id}: not in the PDF's image order" for f in asset.figures
                if f.id not in by_id or by_id[f.id][0] != f.page]
    problems += [f"{f.id}: PyMuPDF reports no placement" for f in asset.figures
                 if f.id in by_id and by_id[f.id][1] is None]
    if problems:
        return LocationPlan("mismatch", problems=problems)
    new = replace(asset, figures=[replace(f, bbox=by_id[f.id][1]) for f in asset.figures])
    return LocationPlan("updated", new)


# What the reader treats as the anchored figure: the first <figure>, figure
# <div> (pandoc writes figure* as <div class="figure*">) or <img> after it.
_ANCHOR_OR_FIGURE = re.compile(
    r'<span id="(phfig-\d+)"></span>|<figure\b|<div class="figure|<img\b', re.IGNORECASE)


def _shows_image(html: str, start: int, tag: str) -> bool:
    """Whether the figure container opening at ``start`` holds an image."""
    if tag.startswith("<img"):
        return True
    close = "</figure>" if tag.startswith("<figure") else "</div>"
    end = html.find(close, start)
    return "<img" in html[start:end if end != -1 else len(html)]


def html_anchor_problems(html: str, figures: list[FigureAsset]) -> list[str]:
    """Figures a citation could not show: the ``phfig`` anchor is missing, no
    figure follows it before the next anchor, or that figure renders no image
    (the reader would scroll to an empty box)."""
    followed: dict[str, bool] = {}
    present: set[str] = set()
    pending: str | None = None
    for m in _ANCHOR_OR_FIGURE.finditer(html):
        if m.group(1):
            pending = m.group(1)
            present.add(pending)
        elif pending is not None:
            followed[pending] = _shows_image(html, m.start(), m.group(0).lower())
            pending = None
    problems = []
    for f in figures:
        dom = figure_dom_id(f.id)
        if dom not in present:
            problems.append(f"{f.id}: no anchor in source.html")
        elif dom not in followed:
            problems.append(f"{f.id}: anchor not followed by a figure")
        elif not followed[dom]:
            problems.append(f"{f.id}: its figure renders no image")
    return problems


def verify_paper(cache_dir: Path, html_path: Path | None) -> list[str]:
    """Every figure the reader cannot open on (empty list = all can)."""
    asset = read_paper_asset(cache_dir)
    figures = [f for f in (asset.figures if asset else [])
               if f.abs_image_path(cache_dir).is_file()]
    if not figures:
        return []
    if reader_mode(cache_dir) == "pdf":
        return [f"{f.id}: no bbox" for f in figures if f.bbox is None]
    if html_path is None or not html_path.is_file():
        return ["no source.html"]
    return html_anchor_problems(html_path.read_text(encoding="utf-8"), figures)


async def _papers(conn: aiosqlite.Connection, only: int | None) -> list[tuple[Any, ...]]:
    sql = ("SELECT id, kind, asset_status, source_path, source_dir_path, html_path "
           "FROM paper_content")
    args: tuple[int, ...] = ()
    if only is not None:
        sql += " WHERE id = ?"
        args = (only,)
    async with conn.execute(sql + " ORDER BY id", args) as cur:
        return [tuple(r) for r in await cur.fetchall()]


def apply_plan(plan: LocationPlan, cache_dir: Path) -> None:
    """Write an updated plan: composites, joined figures, then the asset."""
    if plan.asset is None:
        return
    apply_recompose(plan, cache_dir)
    if plan.merges:
        assert plan.asset is not None
        pdf = sorted(cache_dir.glob("*.pdf"))[0]
        plan.asset = apply_merges(plan.asset, plan.merges, pdf, cache_dir)
    write_paper_asset(plan.asset, cache_dir)


def _with_merges(plan: LocationPlan, cache_dir: Path) -> LocationPlan:
    """Add the joins of figures Marker split into pieces (bboxes known)."""
    loaded = read_block_assets(cache_dir)
    if plan.asset is None or loaded is None:
        return plan
    plan.merges = plan_merges(plan.asset.figures, loaded[0])
    if plan.merges:
        plan.status = "updated"
    return plan


def plan_paper(
    asset_status: str | None, source_path: str | None, cache_dir: Path,
) -> LocationPlan:
    asset = read_paper_asset(cache_dir)
    if asset is None:
        return LocationPlan("no_asset")
    if reader_mode(cache_dir) == "html":
        flat = cache_dir / "source.flattened.tex"
        if source_path is None or not flat.is_file():
            return LocationPlan("mismatch", problems=["no LaTeX source"])
        return plan_latex(asset, Path(source_path).parent,
                          flat.read_text(encoding="utf-8"))
    # asset_status does not always say which extractor wrote the asset (a
    # Marker upgrade can leave it 'pymupdf_only'): use whichever explains
    # every figure.
    del asset_status
    marker = plan_marker(asset, cache_dir)
    if marker.status != "mismatch":
        return _with_merges(marker, cache_dir)
    pymupdf_plan = plan_pymupdf(asset, sorted(cache_dir.glob("*.pdf"))[0])
    if pymupdf_plan.status != "mismatch":
        return pymupdf_plan
    return LocationPlan("mismatch", problems=[
        *(f"marker: {p}" for p in marker.problems),
        *(f"pymupdf: {p}" for p in pymupdf_plan.problems)])


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-backfill-figure-locations",
        description="Record where each figure sits in the reader (idempotent).",
    )
    parser.add_argument("--paper-content-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    parser.add_argument("--verify", action="store_true",
                        help="Only check that every figure can be opened.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = load_settings()
    counts: Counter[str] = Counter()
    async with aiosqlite.connect(settings.db_path) as conn:
        for pid, _kind, status, source_path, sd, html in await _papers(
                conn, args.paper_content_id):
            cache_dir = Path(sd)
            try:
                if args.verify:
                    problems = verify_paper(cache_dir, Path(html) if html else None)
                    counts["unlocatable" if problems else "ok"] += 1
                    if problems:
                        _LOG.warning("pcid=%d: %s", pid, "; ".join(problems))
                    continue
                plan = plan_paper(status, source_path, cache_dir)
                if plan.status == "updated" and plan.asset and not args.dry_run:
                    apply_plan(plan, cache_dir)
            except Exception:  # noqa: BLE001 - per-paper recovery; reported below
                _LOG.exception("pcid=%d: error", pid)
                counts["error"] += 1
                continue
            counts[plan.status] += 1
            if plan.status == "mismatch":
                _LOG.warning("pcid=%d: mismatch: %s", pid, "; ".join(plan.problems))
            elif plan.status == "updated":
                _LOG.info("pcid=%d: updated%s%s", pid,
                          f" (dropped commented-out {plan.dropped})" if plan.dropped else "",
                          f" (composite of all panels: {sorted(plan.recompose)})"
                          if plan.recompose else "")
                for m in plan.merges:
                    _LOG.info("pcid=%d: join split figure %s <- %s (page %d)",
                              pid, m.keep, m.absorbed, m.page)
    _LOG.info("summary%s: %s", " (dry run)" if args.dry_run else "",
              dict(sorted(counts.items())))
    return 1 if counts["error"] or counts["mismatch"] or counts["unlocatable"] else 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
