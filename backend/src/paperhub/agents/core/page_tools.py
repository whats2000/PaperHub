# mypy: disable-error-code="no-untyped-call"
"""Page tools: the agent reads figures, formulas and tables from the paper's
own pages (its page PDF), zooming in for detail (SRS §III-10.4).

Every result carries a ``[view:P/N/x0,y0,x1,y1]`` token — paper, 1-based
page, box in 0–1000 page coordinates — that the answer cites, so a claim read
off a page points at exactly the region the agent saw. Images follow the
step's tool messages (``images_message``); the trace records page, box, DPI
and the PNG's hash, never the pixels.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pymupdf
from PIL import Image

from paperhub.agents.core.figures import IMAGE_MAX_SIDE, load_figures
from paperhub.agents.core.tools import ToolContext, ToolOutcome, error_outcome
from paperhub.pipelines.page_pdf import is_compiled_pdf, page_pdf_path

ZOOM_MAX_DPI = 600
NO_PAGES = ("Paper {paper} has no page image; use view_figure for its figures "
            "and read_chunks for its text.")
BAD_BOX = ("box must be four integers in 0-1000 page coordinates [x0, y0, x1, y1] "
           "with x0 < x1 and y0 < y1.")

Box = tuple[int, int, int, int]


def view_token(paper: int, page: int, box: Box) -> str:
    """``[view:P/N/x0,y0,x1,y1]`` — page 1-based, box in 0–1000."""
    return f"[view:{paper}/{page}/{','.join(str(v) for v in box)}]"


@dataclass(frozen=True)
class PageImage:
    paper: int
    page: int                 # 1-based
    box: Box
    mime: str
    data_b64: str
    size: tuple[int, int]
    sha256: str               # of the PNG sent
    dpi: int

    @property
    def header(self) -> str:
        return (f'<page paper="{self.paper}" page="{self.page}" '
                f'box="{",".join(str(v) for v in self.box)}">')


def to_box(bbox: tuple[float, ...] | list[float], width: float, height: float) -> Box:
    """A PDF-point bbox as a 0–1000 box, rounded outward."""
    def clamp(v: float) -> int:
        return max(0, min(1000, v))  # type: ignore[return-value]

    x0 = clamp(math.floor(bbox[0] / width * 1000))
    y0 = clamp(math.floor(bbox[1] / height * 1000))
    x1 = clamp(math.ceil(bbox[2] / width * 1000))
    y1 = clamp(math.ceil(bbox[3] / height * 1000))
    # A zero-width/-height region (a degenerate stored bbox) still needs a
    # box: widen it by one unit, inward at the page edge.
    if x1 <= x0:
        x0, x1 = (x0, x0 + 1) if x0 < 1000 else (999, 1000)
    if y1 <= y0:
        y0, y1 = (y0, y0 + 1) if y0 < 1000 else (999, 1000)
    return (x0, y0, x1, y1)


def _parse_box(raw: Any) -> Box | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    vals: list[int] = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
            return None
        vals.append(int(v))
    x0, y0, x1, y1 = vals
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return None
    return (x0, y0, x1, y1)


def render_region(
    pdf: Path, page: int, box: Box, *, paper: int, cap_dpi: int = ZOOM_MAX_DPI,
) -> PageImage:
    """Render ``box`` of 1-based ``page``: long side ``IMAGE_MAX_SIDE`` px,
    at most ``cap_dpi``. Blocking: run it in a thread."""
    with pymupdf.open(pdf) as doc:
        p = doc[page - 1]
        w, h = p.rect.width, p.rect.height
        clip = pymupdf.Rect(box[0] / 1000 * w, box[1] / 1000 * h,
                            box[2] / 1000 * w, box[3] / 1000 * h)
        long_pt = max(clip.width, clip.height)
        dpi = min(cap_dpi, IMAGE_MAX_SIDE * 72 / long_pt)
        target = round(long_pt * dpi / 72)
        pix = p.get_pixmap(dpi=math.ceil(dpi), clip=clip)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    scale = target / max(img.size)
    if abs(scale - 1) > 1e-6:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    return PageImage(paper=paper, page=page, box=box, mime="image/png",
                     data_b64=base64.b64encode(data).decode("ascii"), size=img.size,
                     sha256=hashlib.sha256(data).hexdigest(), dpi=round(dpi))


def _page_count(pdf: Path) -> tuple[int, float, float]:
    with pymupdf.open(pdf) as doc:
        first = doc[0].rect
        return doc.page_count, first.width, first.height


def _page_size(pdf: Path, page: int) -> tuple[float, float]:
    with pymupdf.open(pdf) as doc:
        r = doc[page - 1].rect
        return r.width, r.height


async def _source_dir(ctx: ToolContext, paper: int) -> Path | None:
    async with ctx.conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?", (paper,),
    ) as cur:
        row = await cur.fetchone()
    return Path(row[0]) if row and row[0] else None


async def page_pdf_for(ctx: ToolContext, paper: int) -> Path | None:
    src = await _source_dir(ctx, paper)
    return page_pdf_path(src) if src is not None else None


def _record(img: PageImage, pdf: Path, **extra: Any) -> dict[str, Any]:
    return {"paper": img.paper, "page": img.page, "box": list(img.box),
            "sha256": img.sha256, "size": list(img.size), "dpi": img.dpi,
            "pdf": "compiled" if is_compiled_pdf(pdf) else "own", **extra}


async def _checked_page(pdf: Path, paper: int, page: Any) -> str | None:
    count, _w, _h = await asyncio.to_thread(_page_count, pdf)
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= count:
        return f"Page {page!r} is not in paper {paper} (pages 1-{count})."
    return None


async def figures_on_page(
    ctx: ToolContext, paper: int, page: int, width: float, height: float,
) -> list[str]:
    """``fig-003 box=…: caption`` lines for the asset figures on ``page``."""
    _src, figures = await load_figures(ctx.conn, paper)
    lines = []
    for f in figures:
        if f.page == page - 1 and f.bbox is not None:
            box = ",".join(str(v) for v in to_box(f.bbox, width, height))
            cap = " ".join(f.caption.split())[:120]
            lines.append(f"{f.id} box={box}: {cap or '(no caption)'}")
    return lines


async def view_page_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    # read_tools imports this module: import it here, not at the top.
    from paperhub.agents.core.read_tools import _paper_error

    paper, page = args.get("paper"), args.get("page")
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    assert isinstance(paper, int)
    pdf = await page_pdf_for(ctx, paper)
    if pdf is None:
        return error_outcome(NO_PAGES.format(paper=paper))
    if (msg := await _checked_page(pdf, paper, page)) is not None:
        return error_outcome(msg)
    assert isinstance(page, int)
    box: Box = (0, 0, 1000, 1000)
    img = await asyncio.to_thread(render_region, pdf, page, box, paper=paper,
                                  cap_dpi=ZOOM_MAX_DPI)
    w, h = await asyncio.to_thread(_page_size, pdf, page)
    figs = await figures_on_page(ctx, paper, page, w, h)
    listing = ("\nFigures on this page (zoom into a box for detail):\n" + "\n".join(figs)
               if figs else "")
    content = (f'<page paper="{paper}" page="{page}" cite="{view_token(paper, page, box)}">'
               f"{listing}\n</page>\nThe image is in the next message.")
    return ToolOutcome(content=content, record=_record(img, pdf), images=[img])


async def zoom_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    # read_tools imports this module: import it here, not at the top.
    from paperhub.agents.core.read_tools import _paper_error

    paper, page = args.get("paper"), args.get("page")
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    assert isinstance(paper, int)
    pdf = await page_pdf_for(ctx, paper)
    if pdf is None:
        return error_outcome(NO_PAGES.format(paper=paper))
    if (msg := await _checked_page(pdf, paper, page)) is not None:
        return error_outcome(msg)
    box = _parse_box(args.get("box"))
    if box is None:
        return error_outcome(BAD_BOX)
    assert isinstance(page, int)
    img = await asyncio.to_thread(render_region, pdf, page, box, paper=paper)
    token = view_token(paper, page, box)
    content = (f'<zoom paper="{paper}" page="{page}" box="{",".join(map(str, box))}" '
               f'cite="{token}">\nThe image is in the next message.')
    return ToolOutcome(content=content, record=_record(img, pdf), images=[img])


async def view_chunk_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    # read_tools imports this module: import it here, not at the top.
    from paperhub.agents.core.read_tools import _paper_error

    cid = args.get("id")
    if isinstance(cid, bool) or not isinstance(cid, int):
        return error_outcome("id must be a chunk id (integer).")
    async with ctx.conn.execute(
        "SELECT paper_content_id, page, bbox, section FROM chunks WHERE id = ?", (cid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return error_outcome(f"No chunk {cid}.")
    paper = int(row[0])
    if (err := _paper_error(ctx, paper)) is not None:
        return err
    bbox = json.loads(row[2]) if row[2] else None
    pdf = await page_pdf_for(ctx, paper)
    page0: int | None = int(row[1]) if row[1] is not None else None
    located = False
    no_region = page0 is None or not isinstance(bbox, list) or len(bbox) != 4
    if no_region and pdf is not None and not is_compiled_pdf(pdf):
        # A PyMuPDF / early-Marker chunk: find its words on the paper's own PDF.
        found = (await _located_regions(ctx, paper, pdf)).get(cid)
        if found is not None:
            page0, located = found[0], True
            bbox = list(found[1])
    if page0 is None or not isinstance(bbox, list) or len(bbox) != 4 or pdf is None:
        return error_outcome(
            f"Chunk {cid} has no page region on the paper's PDF; read it with read_chunks.")
    page = page0 + 1
    w, h = await asyncio.to_thread(_page_size, pdf, page)
    box = to_box(bbox, w, h)
    img = await asyncio.to_thread(render_region, pdf, page, box, paper=paper)
    token = view_token(paper, page, box)
    content = (f'<chunk-region id="{cid}" paper="{paper}" page="{page}" '
               f'cite="{token}">\n{row[3] or ""}\n</chunk-region>\n'
               "The image is in the next message.")
    record = _record(img, pdf, chunk=cid)
    record["located"] = located
    return ToolOutcome(content=content, record=record, images=[img], chunk_ids_read=[])


_Regions = dict[int, tuple[int, tuple[float, float, float, float]]]
# paper -> ((pdf path, pdf mtime, region-less chunk count, max chunk id), regions):
# one entry per paper, replaced when the PDF or the paper's chunks change
# (a re-chunk gives the rows new ids).
_REGIONS_CACHE: dict[int, tuple[tuple[str, float, int, int], _Regions]] = {}


async def _located_regions(ctx: ToolContext, paper: int, pdf: Path) -> _Regions:
    """Regions of the paper's chunks that have none, found on its own PDF
    (deterministic, so a replay reproduces them; cached per PDF version and
    chunk set)."""
    from paperhub.pipelines.chunk_regions import locate_chunk_regions

    async with ctx.conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks "
        "WHERE paper_content_id = ? AND page IS NULL", (paper,),
    ) as cur:
        row = await cur.fetchone()
    count, max_id = (int(row[0]), int(row[1])) if row else (0, 0)
    key = (str(pdf), pdf.stat().st_mtime, count, max_id)  # noqa: ASYNC240 - one stat
    cached = _REGIONS_CACHE.get(paper)
    if cached is not None and cached[0] == key:
        return cached[1]
    async with ctx.conn.execute(
        "SELECT id, text FROM chunks WHERE paper_content_id = ? AND page IS NULL "
        "ORDER BY id", (paper,),
    ) as cur:
        rows = [(int(r[0]), str(r[1])) for r in await cur.fetchall()]
    regions = await asyncio.to_thread(locate_chunk_regions, pdf, rows)
    _REGIONS_CACHE[paper] = (key, regions)
    return regions


async def view_figure_page(
    ctx: ToolContext, paper: int, figure_id: str, figure_page: int,
    bbox: tuple[float, float, float, float] | None, head: str,
) -> ToolOutcome | None:
    """``view_figure`` on a paper with a page PDF: the figure's page and its box; None when
    the paper has no page PDF (the caller sends the figure image instead)."""
    pdf = await page_pdf_for(ctx, paper)
    if pdf is None:
        return None
    page = figure_page + 1
    count, _w, _h = await asyncio.to_thread(_page_count, pdf)
    if not 1 <= page <= count:
        return None                    # a stale page: the caller sends the image
    w, h = await asyncio.to_thread(_page_size, pdf, page)
    full: Box = (0, 0, 1000, 1000)
    img = await asyncio.to_thread(render_region, pdf, page, full, paper=paper)
    where = (f"figure box: {','.join(str(v) for v in to_box(bbox, w, h))} "
             "(0-1000 page coordinates; zoom into it for detail)"
             if bbox is not None else
             "figure box not known: it is next to its caption; zoom into it for detail")
    content = (f"{head}\nOn page {page}; {where}. "
               f"Page cite: {view_token(paper, page, full)}\n"
               "The page image is in the next message.")
    return ToolOutcome(content=content, record=_record(img, pdf, figure=figure_id),
                       images=[img])


PAGE_TOOLS = ("view_page", "zoom", "view_chunk")


async def rerun_page_tool(
    conn: Any, name: str, args: dict[str, Any], paper: int,
) -> ToolOutcome:
    """Run a page tool (or ``view_figure``) again, outside a
    turn: the feedback export rebuilds what the model saw this way and
    compares the image hash with the recorded one."""
    from paperhub.agents.core.read_tools import _view_figure_tool

    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    ctx = ToolContext(
        conn=conn, tracer=None, session_id=0, run_id=0,  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        adapter=None,  # type: ignore[arg-type]
        emit=emit, enabled_papers=[(paper, "")], history=[], state={},
    )
    handler = {"view_page": view_page_tool, "zoom": zoom_tool,
               "view_chunk": view_chunk_tool, "view_figure": _view_figure_tool}[name]
    return await handler(ctx, args)
