"""Figure images for the core agent.

Every ingested paper keeps its figures as image files in its asset
(``asset/figures.json`` + ``asset/figures/``), whatever the source (LaTeX,
Marker or PyMuPDF). ``list_figures_tables`` lists them by asset id and
``view_figure`` sends one to the model as an image, so a question about what a
plot shows (bar heights, counts, trends) is answered from the figure itself,
not from its caption. The image travels in a user message after the step's
tool results (tool messages carry text only on most providers); the trace
records the file and its hash, never the pixels.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiosqlite
from PIL import Image

from paperhub.agents.paper_qa_subagent import _list_figures_tables
from paperhub.pipelines.paper_asset import FigureAsset, read_paper_asset

# Long-side cap of a sent image: enough to read axis ticks and bar labels,
# small enough that a few figures stay cheap.
IMAGE_MAX_SIDE = 1568
# What one sent image costs against the turn's tool-result token budget (a
# rough provider-neutral estimate for a ~1.5k px image).
IMAGE_TOKENS = 1600
CAPTION_CHARS = 200
_NUMBER_REF = re.compile(r"^\s*fig(?:ure)?\.?\s*(\d+)\s*$", re.IGNORECASE)
IMAGES_HEADER = "Images returned by the tools in the step above:"


@dataclass(frozen=True)
class FigureImage:
    paper: int
    figure_id: str
    caption: str
    page: int | None
    mime: str
    data_b64: str
    size: tuple[int, int]
    sha256: str  # of the source image file

    @property
    def header(self) -> str:
        return f'<figure id="{self.figure_id}" paper="{self.paper}">'


async def _source_dir(conn: aiosqlite.Connection, paper: int) -> Path | None:
    async with conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?", (paper,),
    ) as cur:
        row = await cur.fetchone()
    return Path(row[0]) if row is not None and row[0] else None


async def load_figures(
    conn: aiosqlite.Connection, paper: int,
) -> tuple[Path | None, list[FigureAsset]]:
    """The paper's asset dir and its figures whose image file exists."""
    src = await _source_dir(conn, paper)
    if src is None:
        return None, []
    asset = read_paper_asset(src)
    if asset is None:
        return src, []
    return src, [f for f in asset.figures if f.abs_image_path(src).is_file()]


def _caption_number(caption: str) -> str | None:
    m = re.match(r"^\s*fig(?:ure)?\.?\s*(\d+)\b", caption, re.IGNORECASE)
    return m.group(1) if m else None


def resolve_figure(figures: list[FigureAsset], ref: str) -> FigureAsset | None:
    """A figure by asset id (``fig-003``) or by its number as the caption
    gives it (``Figure 3`` / ``Fig. 3``)."""
    key = ref.strip().casefold()
    for f in figures:
        if f.id.casefold() == key:
            return f
    m = _NUMBER_REF.match(ref)
    if m is None:
        return None
    return next((f for f in figures if _caption_number(f.caption) == m.group(1)), None)


def _figure_line(f: FigureAsset) -> str:
    page = f" (p.{f.page + 1})" if f.page is not None else ""
    cap = " ".join(f.caption.split())
    if len(cap) > CAPTION_CHARS:
        cap = cap[:CAPTION_CHARS].rstrip() + "…"
    return f"- {f.id}{page}: {cap or '(no caption)'}"


async def figures_tables_text(
    *, paper_content_id: int, conn: aiosqlite.Connection,
) -> tuple[str, list[str], list[str]]:
    """What ``list_figures_tables`` shows: the layout index (citable figure
    and table chunks) plus the figure images ``view_figure`` can open.
    Returns ``(text, layout labels, figure ids)``; shared with the feedback
    export, which rebuilds the same text."""
    layout_text, labels = await _list_figures_tables(
        paper_content_id=paper_content_id, conn=conn)
    _src, figures = await load_figures(conn, paper_content_id)
    if not figures:
        return layout_text, labels, []
    images = "Figure images (open one with view_figure):\n" + "\n".join(
        _figure_line(f) for f in figures)
    text = images if layout_text == "(none)" else f"{layout_text}\n\n{images}"
    return text, labels, [f.id for f in figures]


def encode_image(path: Path) -> tuple[str, str, tuple[int, int], str]:
    """``(mime, base64, size, sha256 of the file)``; downscaled to
    ``IMAGE_MAX_SIDE``. Blocking: run it in a thread."""
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        if max(im.size) > IMAGE_MAX_SIDE:
            im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
        keep_png = path.suffix.lower() == ".png" or im.mode in ("RGBA", "LA", "P")
        buf = io.BytesIO()
        if keep_png:
            im.save(buf, format="PNG", optimize=True)
            mime = "image/png"
        else:
            im.convert("RGB").save(buf, format="JPEG", quality=90)
            mime = "image/jpeg"
        size = (im.size[0], im.size[1])
    return mime, base64.b64encode(buf.getvalue()).decode("ascii"), size, sha


def encode_figure(paper: int, src: Path, f: FigureAsset) -> FigureImage:
    mime, data, size, sha = encode_image(f.abs_image_path(src))
    return FigureImage(paper=paper, figure_id=f.id, caption=f.caption, page=f.page,
                       mime=mime, data_b64=data, size=size, sha256=sha)


def figure_content(paper: int, f: FigureAsset) -> str:
    """The tool message ``view_figure`` returns (the image follows it)."""
    page = f' page="{f.page + 1}"' if f.page is not None else ""
    cap = " ".join(f.caption.split()) or "(no caption)"
    return (f'<figure id="{f.id}" paper="{paper}"{page} cite="[fig:{paper}/{f.id}]">'
            f"\n{cap}\n</figure>\nThe image is in the next message.")


class SentImage(Protocol):
    """A figure or page image sent to the model."""

    @property
    def header(self) -> str: ...
    @property
    def mime(self) -> str: ...
    @property
    def data_b64(self) -> str: ...


def images_message(images: Sequence[SentImage]) -> dict[str, Any]:
    """One user message carrying the step's images, each headed by what it
    is (figure id, or page and box) so the model can tell several apart."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": IMAGES_HEADER}]
    for img in images:
        parts.append({"type": "text", "text": img.header})
        parts.append({"type": "image_url", "image_url": {
            "url": f"data:{img.mime};base64,{img.data_b64}"}})
    return {"role": "user", "content": parts}
