# mypy: disable-error-code="no-untyped-call"
"""Place LaTeX figures on their compiled page (the page PDF).

A figure is found by its caption: the first page whose text holds the
caption's opening words. Its box is the union of the images and vector
drawings in the caption's column between the caption and the nearest prose
paragraph (or another caption) above it — or below it, for a caption placed
above its figure. A figure without a caption is found by an embedded image
of the same pixel size. Figures not found are left out; their citations still
open the figure in the HTML reader.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from pathlib import Path

import pymupdf
from PIL import Image

from paperhub.pipelines.page_pdf import page_pdf_path
from paperhub.pipelines.paper_asset import FigureAsset, PaperAsset, read_paper_asset

Box = tuple[float, float, float, float]
_CAPTION_START = re.compile(r"^\s*(?:Figure|Fig\.?|Table)\s*\d+", re.IGNORECASE)
CAPTION_WORDS = 8
FUZZY_WORDS = 10
FUZZY_SHARE = 0.8
MIN_CAPTION_WORDS = 3
PROSE_CHARS = 100
COMPACT_KEY_CHARS = 40     # caption characters compared space-insensitively
_LABEL_END_RE = re.compile(r"(?:figure|fig)\d+[a-z]?$")


def _norm(word: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", unicodedata.normalize("NFKC", word).lower())


def _compact_words(page: pymupdf.Page) -> tuple[str, list[tuple[int, pymupdf.Rect]]]:
    """The page's words as one lower-case alphanumeric string (spaces and
    line breaks dropped) and, per word, where it starts in it and its rect."""
    parts: list[str] = []
    starts: list[tuple[int, pymupdf.Rect]] = []
    at = 0
    for w in page.get_text("words"):
        n = _norm(str(w[4]))
        starts.append((at, pymupdf.Rect(w[:4])))
        parts.append(n)
        at += len(n)
    return "".join(parts), starts


def _word_at(starts: list[tuple[int, pymupdf.Rect]], offset: int) -> pymupdf.Rect:
    rect = starts[0][1]
    for at, r in starts:
        if at > offset:
            break
        rect = r
    return rect


def _compact_hit(
    doc: pymupdf.Document, caption: str, *, after_label: bool,
) -> tuple[int, pymupdf.Rect] | None:
    """The caption's start found with spaces ignored (fonts that drop the
    spaces of a bold run). ``after_label``: accept only an occurrence that
    directly follows a printed 'Figure N' / 'Fig. N' — a short caption also
    appears in body text."""
    key = _norm(caption)[:COMPACT_KEY_CHARS]
    if not key:
        return None
    for pno in range(doc.page_count):
        text, starts = _compact_words(doc[pno])
        at = text.find(key)
        while at >= 0:
            if not after_label or _LABEL_END_RE.search(text[max(0, at - 12):at]):
                return pno, _word_at(starts, at)
            at = text.find(key, at + 1)
    return None


def _caption_hit(doc: pymupdf.Document, caption: str) -> tuple[int, pymupdf.Rect] | None:
    words = caption.split()
    if len(words) < MIN_CAPTION_WORDS:
        return _compact_hit(doc, caption, after_label=True)
    for n in (CAPTION_WORDS, 5):
        phrase = " ".join(words[:n])
        for pno in range(doc.page_count):
            hits = doc[pno].search_for(phrase)
            if hits:
                return pno, hits[0]
    return _caption_words_hit(doc, caption) or _compact_hit(doc, caption, after_label=False)


def _caption_words_hit(doc: pymupdf.Document, caption: str) -> tuple[int, pymupdf.Rect] | None:
    """The caption's first words matched in order on a page, skipping the
    words its cleaned LaTeX lost (macros, math) and undoing ligatures."""
    want = [w for w in (_norm(t) for t in re.findall(r"[^\s{}$]+", caption))
            if len(w) >= 3 and not w.isdigit()][:FUZZY_WORDS]
    if len(want) < MIN_CAPTION_WORDS:
        return None
    for pno in range(doc.page_count):
        page_words = doc[pno].get_text("words")
        norm = [_norm(str(w[4])) for w in page_words]
        for start, first in enumerate(norm):
            if first not in want[:3]:
                continue
            k = want.index(first) + 1
            matched = k
            j = start + 1
            limit = start + 3 * len(want)
            while j < min(len(norm), limit) and k < len(want):
                if norm[j] == want[k]:
                    matched += 1
                    k += 1
                elif norm[j] in want[k + 1:k + 3]:
                    k = want.index(norm[j], k) + 1
                    matched += 1
                j += 1
            if matched >= FUZZY_SHARE * len(want):
                w = page_words[start]
                return pno, pymupdf.Rect(w[0], w[1], w[2], w[3])
    return None


def _column(page: pymupdf.Page, cap: pymupdf.Rect) -> tuple[float, float]:
    mid = page.rect.width / 2
    if cap.x0 < mid - 20 and cap.x1 > mid + 20:
        return (0.0, float(page.rect.width))
    return (0.0, mid) if (cap.x0 + cap.x1) / 2 < mid else (mid, float(page.rect.width))


def _overlaps(r: pymupdf.Rect, col: tuple[float, float]) -> bool:
    return min(r.x1, col[1]) - max(r.x0, col[0]) > 1


def _drawn_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Each drawing's visible rect: clipped by the clip paths active at its
    nesting level and by the page (plot bars are often drawn far wider than
    shown and trimmed by a clip)."""
    out: list[pymupdf.Rect] = []
    clips: list[tuple[int, pymupdf.Rect]] = []
    for item in page.get_drawings(extended=True):
        level = int(item.get("level", 0) or 0)
        while clips and clips[-1][0] >= level:
            clips.pop()
        kind = item.get("type")
        if kind == "clip":
            r = pymupdf.Rect(item["scissor"])
            if clips:
                r &= clips[-1][1]
            clips.append((level, r))
            continue
        if kind == "group" or "rect" not in item:
            continue
        r = pymupdf.Rect(item["rect"])
        if clips:
            r &= clips[-1][1]
        out.append(r & page.rect)
    return out


def _graphics(page: pymupdf.Page) -> list[pymupdf.Rect]:
    # Images keep their raw rect: their clip paths are not reported, and an
    # image hanging off the page is left out by the box's top/bottom limits
    # (clipping it to the page would let it in — paper 16).
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    rects += _drawn_rects(page)
    full = page.rect
    # Page-sized backgrounds and empty rects are not figure content.
    return [r for r in rects if not r.is_empty and not (
        r.width > 0.9 * full.width and r.height > 0.9 * full.height)]


def _stops(page: pymupdf.Page, col: tuple[float, float], cap: pymupdf.Rect) -> list[pymupdf.Rect]:
    """Prose paragraphs and other captions in the column: a figure's limits."""
    out = []
    for b in page.get_text("blocks"):
        rect, text = pymupdf.Rect(b[:4]), str(b[4])
        if rect.intersects(cap) or not _overlaps(rect, col):
            continue
        if len(" ".join(text.split())) >= PROSE_CHARS or _CAPTION_START.match(text):
            out.append(rect)
    return out


def _union(rects: list[pymupdf.Rect]) -> Box:
    return (min(r.x0 for r in rects), min(r.y0 for r in rects),
            max(r.x1 for r in rects), max(r.y1 for r in rects))


def _box_for_caption(page: pymupdf.Page, cap: pymupdf.Rect) -> Box | None:
    col = _column(page, cap)
    graphics = [g for g in _graphics(page) if _overlaps(g, col)]
    stops = _stops(page, col, cap)
    top = max((s.y1 for s in stops if s.y1 <= cap.y0 + 1), default=0.0)
    above = [g for g in graphics if g.y0 >= top - 1 and g.y1 <= cap.y0 + 1]
    if above:
        return _union(above)
    cap_block = next((pymupdf.Rect(b[:4]) for b in page.get_text("blocks")
                      if pymupdf.Rect(b[:4]).intersects(cap)), cap)
    bottom = min((s.y0 for s in stops if s.y0 >= cap_block.y1 - 1),
                 default=float(page.rect.height))
    below = [g for g in graphics if g.y0 >= cap_block.y1 - 1 and g.y1 <= bottom + 1]
    return _union(below) if below else None


def _by_image_size(doc: pymupdf.Document, size: tuple[int, int]) -> tuple[int, Box] | None:
    """The one placed image of exactly ``size`` pixels; None when the size
    occurs zero or several times (figures exported at one figsize share
    pixel sizes, so a repeated size identifies nothing)."""
    hits: list[tuple[int, Box]] = []
    for pno in range(doc.page_count):
        for info in doc[pno].get_image_info():
            if (info.get("width"), info.get("height")) == size:
                r = pymupdf.Rect(info["bbox"])
                hits.append((pno, (r.x0, r.y0, r.x1, r.y1)))
    return hits[0] if len(hits) == 1 else None


def locate_latex_figures(
    pdf: Path, figures: list[FigureAsset], image_sizes: dict[str, tuple[int, int]],
) -> dict[str, tuple[int, Box | None]]:
    """``{figure id: (0-based page, box in PDF points or None)}`` for the
    figures found — the box is None when the caption was found but no
    graphics around it (text-built figures); ``image_sizes`` gives a figure's
    raster size, for figures whose caption is missing or not found."""
    found: dict[str, tuple[int, Box | None]] = {}
    with pymupdf.open(pdf) as doc:
        for f in figures:
            hit = _caption_hit(doc, f.caption)
            if hit is not None:
                found[f.id] = (hit[0], _box_for_caption(doc[hit[0]], hit[1]))
                continue
            size = image_sizes.get(f.id)
            # Only a size no other figure has: two figures of one size would
            # both claim the same image.
            shared = size is not None and sum(
                1 for v in image_sizes.values() if v == size) > 1
            if size is not None and not shared:
                placed = _by_image_size(doc, size)
                if placed is not None:
                    found[f.id] = placed
    return found


def place_latex_figures(cache_dir: Path, asset: PaperAsset | None = None) -> PaperAsset | None:
    """The paper's asset (``asset``, else the one on disk) with its LaTeX
    figures (``env_index`` set) placed on the page PDF: ``page`` and ``bbox``
    filled for each one found. None when the paper has no asset or no page
    PDF. Does not write."""
    if asset is None:
        asset = read_paper_asset(cache_dir)
    pdf = page_pdf_path(cache_dir)
    if asset is None or pdf is None:
        return None
    latex = [f for f in asset.figures if f.env_index is not None]
    sizes: dict[str, tuple[int, int]] = {}
    for f in latex:
        try:
            with Image.open(f.abs_image_path(cache_dir)) as im:
                sizes[f.id] = (im.width, im.height)
        except OSError:
            continue
    placed = locate_latex_figures(pdf, latex, sizes)
    return replace(asset, figures=[
        replace(f, page=placed[f.id][0], bbox=placed[f.id][1]) if f.id in placed else f
        for f in asset.figures])
