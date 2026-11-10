"""Join a PDF figure that Marker split into pieces back into one figure.

Marker sometimes returns one figure as several picture blocks: a diagram
above a result grid (V-JEPA Fig. 6), panels (a)(b) above panel (c) (V-JEPA 2
Fig. 14), or panels whose "Figure N" caption it tagged as plain text
(V-JEPA 2 Fig. 15). The asset then holds a captioned piece plus caption-less
pieces, and a citation or ``view_figure`` shows only part of the figure.

A figure grows upward from its caption: through caption-less picture blocks
and the short labels between panels ("(a) ...", axis titles), stopping at a
paragraph of prose, a captioned figure or the top of the page. The joined
figure keeps the captioned piece's id (or the first piece's, when only a
"Figure N" caption block names the group), takes the whole region's bbox, and
gets its image re-cropped from the PDF; the absorbed pieces leave the asset.
"""
from __future__ import annotations

import html as html_lib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import pymupdf

from paperhub.pipelines.paper_asset import FigureAsset, PaperAsset, paper_asset_dir

_PICTURES = ("Figure", "Picture")
_GROUPS = ("FigureGroup", "PictureGroup")
_LABEL_TYPES = ("Text", "SectionHeader", "Caption")
_TAG = re.compile(r"<[^>]+>")
_MAIN_CAPTION = re.compile(r"^\s*(?:Figure|Fig\.?)\s*\d+", re.IGNORECASE)
_PANEL_LABEL = re.compile(r"^\s*\(?[a-h]\)\s")
# Text this short between pictures is a label (axis title, panel name).
SHORT_LABEL_CHARS = 80
CROP_DPI = 192


class Block(Protocol):
    @property
    def block_type(self) -> str: ...
    @property
    def page(self) -> int | None: ...
    @property
    def html(self) -> str: ...
    @property
    def bbox(self) -> Sequence[float]: ...


@dataclass
class Merge:
    keep: str                       # the figure id that stays
    absorbed: list[str]             # ids merged into it
    bbox: tuple[float, float, float, float]
    page: int
    caption: str | None             # a main caption to adopt, if any


def _text(block: Block) -> str:
    return " ".join(html_lib.unescape(_TAG.sub(" ", block.html or "")).split())


def _is_label(block: Block) -> bool:
    if block.block_type not in _LABEL_TYPES:
        return False
    text = _text(block)
    if _MAIN_CAPTION.match(text):
        return False
    return bool(_PANEL_LABEL.match(text)) or len(text) <= SHORT_LABEL_CHARS


def _union(boxes: list[Sequence[float]]) -> tuple[float, float, float, float]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _same(a: Sequence[float], b: Sequence[float] | None) -> bool:
    return (b is not None and len(a) == 4 and len(b) == 4
            and all(abs(x - y) < 0.5 for x, y in zip(a, b, strict=True)))


def _grow(
    flow: list[Block], fig_of: dict[int, FigureAsset], claimed: set[int], start: int,
) -> tuple[list[int], list[int]]:
    """Caption-less picture and label indexes above ``start`` belonging to it."""
    pics: list[int] = []
    labels: list[int] = []
    j = start - 1
    while j >= 0:
        b = flow[j]
        if b.block_type in _PICTURES:
            f = fig_of.get(j)
            if f is None or f.caption.strip() or j in claimed:
                break
            pics.append(j)
        elif _is_label(b):
            labels.append(j)
        else:
            break
        j -= 1
    return pics, labels


def plan_merges(figures: list[FigureAsset], blocks: Sequence[Block]) -> list[Merge]:
    """The joins to make, page by page (figures need ``page`` and ``bbox``)."""
    merges: list[Merge] = []
    pages = sorted({b.page for b in blocks if b.page is not None})
    for page in pages:
        flow = [b for b in blocks if b.page == page and b.block_type not in _GROUPS]
        fig_of: dict[int, FigureAsset] = {}
        for i, b in enumerate(flow):
            if b.block_type in _PICTURES:
                hit = next((f for f in figures if f.page == page and _same(b.bbox, f.bbox)), None)
                if hit is not None:
                    fig_of[i] = hit
        claimed: set[int] = set()
        for i, b in enumerate(flow):
            f = fig_of.get(i)
            if f is not None and f.caption.strip():
                pics, labels = _grow(flow, fig_of, claimed, i)
                if not pics:
                    continue
                claimed.update(pics)
                caption = None
                if _PANEL_LABEL.match(f.caption):
                    nxt = next((_text(x) for x in flow[i + 1:]
                                if _MAIN_CAPTION.match(_text(x))), None)
                    caption = nxt
                merges.append(Merge(
                    keep=f.id, absorbed=[fig_of[j].id for j in reversed(pics)],
                    bbox=_union([b.bbox, *(flow[j].bbox for j in pics + labels)]),
                    page=page, caption=caption))
            elif (f is None and b.block_type in _LABEL_TYPES
                  and _MAIN_CAPTION.match(_text(b))):
                pics, labels = _grow(flow, fig_of, claimed, i)
                if len(pics) < 2:
                    continue
                claimed.update(pics)
                ordered = [fig_of[j] for j in reversed(pics)]
                merges.append(Merge(
                    keep=ordered[0].id, absorbed=[g.id for g in ordered[1:]],
                    bbox=_union([flow[j].bbox for j in pics + labels]),
                    page=page, caption=_text(b)))
    return merges


def apply_merges(
    asset: PaperAsset, merges: list[Merge], pdf_path: Path, cache_dir: Path,
) -> PaperAsset:
    """The asset with each join made: the kept figure's image is the PDF
    region of the whole figure; absorbed pieces are removed."""
    if not merges:
        return asset
    gone = {fid for m in merges for fid in m.absorbed}
    by_keep = {m.keep: m for m in merges}
    figs: list[FigureAsset] = []
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        for f in asset.figures:
            if f.id in gone:
                continue
            m = by_keep.get(f.id)
            if m is not None:
                clip = pymupdf.Rect(*m.bbox)  # type: ignore[no-untyped-call]
                pix = doc[m.page].get_pixmap(dpi=CROP_DPI, clip=clip)
                name = f"{f.id}.png"
                pix.save(str(paper_asset_dir(cache_dir) / "figures" / name))
                f = replace(f, image_path=f"figures/{name}", bbox=m.bbox,
                            caption=m.caption or f.caption)
            figs.append(f)
    return replace(asset, figures=figs)


def join_split_figures(
    asset: PaperAsset, blocks: Sequence[Block], pdf_path: Path, cache_dir: Path,
) -> PaperAsset:
    """``asset`` with every figure Marker split into pieces joined."""
    return apply_merges(asset, plan_merges(asset.figures, blocks), pdf_path, cache_dir)
