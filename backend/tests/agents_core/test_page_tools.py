"""Page tools: the agent views pages of the paper's page PDF, zooms into a
box, views a chunk's region; each result carries a [view:] token."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pymupdf
import pytest
from PIL import Image

from paperhub.agents.core.page_tools import view_token
from paperhub.agents.core.read_tools import register_read_tools
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.tracing.tracer import Tracer


def _png(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


async def _pdf_paper(conn: aiosqlite.Connection, root: Path, *, own_pdf: bool = True) -> int:
    """A PDF paper (2 Letter pages) with one Marker figure and one chunk."""
    root.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - test fixture
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(100, 100, 500, 300), stream=_png((400, 200)),
                      keep_proportion=False)
    page.insert_text((72, 400), "E = mc^2", fontsize=12)
    if own_pdf:
        doc.save(root / "source.pdf")
    asset = root / "asset"
    (asset / "figures").mkdir(parents=True)
    Image.new("RGB", (400, 200)).save(asset / "figures" / "fig-001.png")
    (asset / "figures.json").write_text(json.dumps([{
        "id": "fig-001", "caption": "Figure 1: A plot.", "page": 1, "section": None,
        "image_path": "figures/fig-001.png", "bbox": [100, 100, 500, 300]}]),
        encoding="utf-8")
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path) VALUES (?, 'pdf_upload', ?, 'P', ?, ?, '/x')",
        (f"sha256:{root.name}", root.name, str(root / "source.pdf"), str(root)))
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pid = int(row[0])
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, page, "
        "bbox) VALUES (?, 'Method', 0, 8, 'E = mc^2', 1, ?)",
        (pid, json.dumps([70.0, 385.0, 160.0, 405.0])))
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
        "VALUES (?, 'Intro', 8, 20, 'no region here')", (pid,))
    await conn.commit()
    return pid


def _ctx(conn: aiosqlite.Connection, tracer: Tracer, pid: int) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    settings = MagicMock()
    return ToolContext(conn=conn, tracer=tracer, session_id=1, run_id=1,
                       settings=settings, adapter=MagicMock(), emit=emit,
                       enabled_papers=[(pid, "P")], history=[], state={})


def _reg(vision: bool = True) -> ToolRegistry:
    r = ToolRegistry()
    register_read_tools(r, vision=vision)
    return r


async def _run(r: ToolRegistry, ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    return await r.run(ctx, ToolCall(id="c", name=name, arguments=args))


def _size(out: ToolOutcome) -> tuple[int, int]:
    img = Image.open(io.BytesIO(base64.b64decode(out.images[0].data_b64)))
    return img.size


def test_view_token_format() -> None:
    assert view_token(20, 5, (120, 340, 560, 720)) == "[view:20/5/120,340,560,720]"


@pytest.mark.asyncio
async def test_view_page_image_and_token(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    out = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "view_page", paper=pid, page=2)
    assert not out.record.get("error")
    assert f"[view:{pid}/2/0,0,1000,1000]" in out.content
    assert "fig-001" in out.content                 # figures on this page are listed
    assert max(_size(out)) == 1568
    assert out.record["page"] == 2 and len(out.record["sha256"]) == 64
    assert "data_b64" not in json.dumps(out.record)


@pytest.mark.asyncio
async def test_zoom_renders_the_box_at_high_dpi(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    out = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "zoom",
                     paper=pid, page=2, box=[100, 100, 300, 200])
    assert not out.record.get("error")
    assert f"[view:{pid}/2/100,100,300,200]" in out.content
    w, h = _size(out)
    # A 122x79 pt region would need ~922 DPI for 1568 px: capped at 600 DPI.
    assert abs(max(w, h) - 1020) <= 2 and abs(w / h - (0.2 * 612) / (0.1 * 792)) < 0.02
    assert out.record["dpi"] == 600
    wide = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "zoom",
                      paper=pid, page=2, box=[0, 0, 1000, 500])
    assert max(_size(wide)) == 1568                 # larger regions: the long-side cap


@pytest.mark.asyncio
@pytest.mark.parametrize("box, page", [
    ([300, 100, 100, 200], 2),      # x0 >= x1
    ([0, 0, 1001, 50], 2),          # out of range
    ([1, 2, 3], 2),                 # not four numbers
    ([10, 10, 20, 20], 9),          # page beyond the document
])
async def test_zoom_rejects_bad_requests(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    box: list[int], page: int,
) -> None:
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    out = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "zoom",
                     paper=pid, page=page, box=box)
    assert out.record.get("error") and out.images == []
    if page == 9:
        assert "1-2" in out.content


@pytest.mark.asyncio
async def test_view_chunk_uses_the_chunk_region(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    async with migrated_db.execute(
        "SELECT id FROM chunks WHERE paper_content_id = ? ORDER BY id", (pid,)) as cur:
        with_region, without = [r[0] for r in await cur.fetchall()]
    ctx = _ctx(migrated_db, fake_tracer, pid)
    out = await _run(_reg(), ctx, "view_chunk", id=with_region)
    assert not out.record.get("error") and out.record["chunk"] == with_region
    # 70..160 x 385..405 pt on a 612x792 page, rounded outward.
    assert f"[view:{pid}/2/114,486,262,512]" in out.content
    none = await _run(_reg(), ctx, "view_chunk", id=without)
    assert none.record.get("error") and "read_chunks" in none.content


@pytest.mark.asyncio
async def test_view_chunk_finds_a_chunk_without_a_region_on_the_papers_pdf(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """A PyMuPDF chunk carries no page region: view_chunk finds its words on
    the paper's own PDF; the chunk row is left as it was."""
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    async with migrated_db.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
        "VALUES (?, 'Method', 20, 28, 'E = mc^2') RETURNING id", (pid,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    cid = int(row[0])
    out = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "view_chunk", id=cid)
    assert not out.record.get("error"), out.content
    assert out.record["chunk"] == cid and out.record["page"] == 2
    assert out.record["located"] is True
    assert f'page="2" cite="[view:{pid}/2/' in out.content
    async with migrated_db.execute("SELECT page, bbox FROM chunks WHERE id = ?", (cid,)) as cur:
        assert await cur.fetchone() == (None, None)


@pytest.mark.asyncio
async def test_view_chunk_finds_chunks_after_a_rechunk(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """A re-chunk gives the chunks new ids while the PDF stays the same: the
    located regions follow the new rows instead of a stale cache."""
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    ctx = _ctx(migrated_db, fake_tracer, pid)

    async def add_chunk() -> int:
        async with migrated_db.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, 'Method', 20, 28, 'E = mc^2') RETURNING id", (pid,)) as cur:
            row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    first = await add_chunk()
    assert not (await _run(_reg(), ctx, "view_chunk", id=first)).record.get("error")
    await migrated_db.execute("DELETE FROM chunks WHERE id = ?", (first,))
    second = await add_chunk()
    out = await _run(_reg(), ctx, "view_chunk", id=second)
    assert not out.record.get("error"), out.content
    assert out.record["located"] is True


@pytest.mark.asyncio
async def test_view_figure_pages_mode_returns_page_and_box(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    out = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "view_figure",
                     paper=pid, figure="Figure 1")
    assert not out.record.get("error")
    assert f'cite="[fig:{pid}/fig-001]"' in out.content
    assert "figure box: 163,126,817,379" in out.content
    assert f"[view:{pid}/2/0,0,1000,1000]" in out.content
    assert out.record["figure"] == "fig-001" and out.record["page"] == 2


@pytest.mark.asyncio
async def test_no_page_pdf_falls_back(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _pdf_paper(migrated_db, tmp_path / "p", own_pdf=False)
    ctx = _ctx(migrated_db, fake_tracer, pid)
    page = await _run(_reg(), ctx, "view_page", paper=pid, page=1)
    assert page.record.get("error") and "no page image" in page.content
    fig = await _run(_reg(), ctx, "view_figure", paper=pid, figure="fig-001")
    assert not fig.record.get("error") and "[view:" not in fig.content
    assert _size(fig) == (400, 200)                 # the figure image instead


def test_tool_sets_by_vision() -> None:
    def names(r: ToolRegistry) -> set[str]:
        return {s["function"]["name"] for s in r.schemas()}

    assert {"view_page", "zoom", "view_chunk", "view_figure"} <= names(_reg())
    blind = names(_reg(vision=False))
    assert not {"view_page", "zoom", "view_chunk", "view_figure"} & blind


@pytest.mark.asyncio
async def test_view_figure_with_a_page_but_no_box(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    """A text-built LaTeX figure: its caption was found (page known) but no
    graphics around it — the page is still sent, the box said to be unknown."""
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    fj = tmp_path / "p" / "asset" / "figures.json"
    data = json.loads(fj.read_text(encoding="utf-8"))
    data[0]["bbox"] = None
    fj.write_text(json.dumps(data), encoding="utf-8")
    out = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "view_figure",
                     paper=pid, figure="fig-001")
    assert "figure box not known" in out.content and out.record["page"] == 2
    assert max(_size(out)) == 1568


def test_to_box_never_returns_an_empty_box() -> None:
    """A zero-width or zero-height stored region still renders and validates."""
    from paperhub.agents.core.page_tools import to_box

    x0, y0, x1, y1 = to_box([100.0, 200.0, 100.0, 200.0], 612.0, 792.0)
    assert x1 > x0 and y1 > y0
    assert to_box([0.0, 0.0, 612.0, 792.0], 612.0, 792.0) == (0, 0, 1000, 1000)
    edge = to_box([612.0, 792.0, 612.0, 792.0], 612.0, 792.0)
    assert edge[2] > edge[0] and edge[3] > edge[1] and edge[2] <= 1000 and edge[3] <= 1000


@pytest.mark.asyncio
async def test_view_figure_with_a_page_beyond_the_pdf_falls_back_to_the_image(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
) -> None:
    pid = await _pdf_paper(migrated_db, tmp_path / "p")
    figs = tmp_path / "p" / "asset" / "figures.json"
    data = json.loads(figs.read_text(encoding="utf-8"))
    data[0]["page"] = 40                                  # stale: the PDF has 2 pages
    figs.write_text(json.dumps(data), encoding="utf-8")
    out = await _run(_reg(), _ctx(migrated_db, fake_tracer, pid), "view_figure",
                     paper=pid, figure="fig-001")
    assert not out.record.get("error"), out.content
    assert "[view:" not in out.content and _size(out) == (400, 200)
