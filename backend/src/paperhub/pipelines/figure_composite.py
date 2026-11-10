"""Compose a multi-panel LaTeX figure into one image.

A figure environment holding several ``\\includegraphics`` (sub-figures,
panels side by side) is one figure in the paper. The asset keeps it as one
image, laid out the way the source arranges it: panels flow left to right at
their ``width=`` share of the line, a new row starts at ``\\\\`` / ``\\par`` /
a blank line or when the line is full, and a panel's ``(a) caption`` is
written under it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CANVAS_WIDTH = 1600
GAP = 16
LABEL_SIZE = 24
# Assumed line width for absolute widths (inches) in a two-column paper.
LINE_INCHES = 6.5

_WIDTH_REL = re.compile(
    r"width\s*=\s*([\d.]*)\s*\\(linewidth|textwidth|columnwidth|hsize)")
_WIDTH_ABS = re.compile(r"width\s*=\s*([\d.]+)\s*(in|cm|mm|pt)")
_UNIT_INCHES = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "pt": 1 / 72.27}


@dataclass(frozen=True)
class Panel:
    path: Path                 # the panel's raster image
    width: float | None        # share of the line, from width=..., if given
    new_row: bool              # a line break comes before it
    label: str | None          # "(a) caption", if the panel has one


def width_share(options: str) -> float | None:
    """A panel's width as a share of the line, from ``\\includegraphics``
    options, or None when only a height (or nothing) is given."""
    m = _WIDTH_REL.search(options)
    if m:
        return float(m.group(1)) if m.group(1) else 1.0
    m = _WIDTH_ABS.search(options)
    if m:
        return min(1.0, float(m.group(1)) * _UNIT_INCHES[m.group(2)] / LINE_INCHES)
    return None


def _rows(panels: list[Panel]) -> list[list[Panel]]:
    default = 1.0 / min(len(panels), 3)
    rows: list[list[Panel]] = []
    used = 0.0
    for p in panels:
        share = p.width or default
        if not rows or p.new_row or used + share > 1.001:
            rows.append([])
            used = 0.0
        rows[-1].append(p)
        used += share
    return rows


def compose_panels(panels: list[Panel], out: Path) -> tuple[int, int]:
    """Write the composite PNG to ``out``; returns its size."""
    font = ImageFont.load_default(size=LABEL_SIZE)
    default = 1.0 / min(len(panels), 3)
    placed: list[tuple[int, list[tuple[Image.Image, str | None]]]] = []
    for row in _rows(panels):
        shares = [p.width or default for p in row]
        scale = min(1.0, 1.0 / sum(shares))
        items: list[tuple[Image.Image, str | None]] = []
        for p, share in zip(row, shares, strict=True):
            with Image.open(p.path) as im:
                img = im.convert("RGB")
            w = max(1, int((CANVAS_WIDTH - GAP * (len(row) - 1)) * share * scale))
            h = max(1, round(img.height * w / img.width))
            items.append((img.resize((w, h), Image.Resampling.LANCZOS), p.label))
        label_h = LABEL_SIZE + GAP if any(lbl for _, lbl in items) else 0
        placed.append((max(i.height for i, _ in items) + label_h, items))
    height = sum(h for h, _ in placed) + GAP * (len(placed) - 1)
    canvas = Image.new("RGB", (CANVAS_WIDTH, height), "white")
    draw = ImageDraw.Draw(canvas)
    y = 0
    for row_h, items in placed:
        row_w = sum(i.width for i, _ in items) + GAP * (len(items) - 1)
        x = (CANVAS_WIDTH - row_w) // 2
        img_h = row_h - (LABEL_SIZE + GAP if any(lbl for _, lbl in items) else 0)
        for img, label in items:
            canvas.paste(img, (x, y))
            if label:
                tw = draw.textlength(label, font=font)
                draw.text((x + (img.width - tw) / 2, y + img_h + GAP // 2), label,
                          fill="black", font=font)
            x += img.width + GAP
        y += row_h + GAP
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG", optimize=True)
    return canvas.size
