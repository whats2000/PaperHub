"""Reader APIs for evidence pages: the page PDF, figure locators and
[view:] regions in PDF points."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


def _pdf(path: Path, pages: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    doc.save(path)


async def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """A LaTeX paper (HTML reader + compiled page PDF + anchored figure), a
    PDF paper (own PDF) and a paper without any page PDF."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    latex = tmp_path / "cache" / "latex"
    (latex / "asset" / "figures").mkdir(parents=True)
    _pdf(latex / "asset" / "paper.pdf")
    Image.new("RGB", (30, 20)).save(latex / "asset" / "figures" / "fig-003.png")
    (latex / "asset" / "figures.json").write_text(json.dumps([{
        "id": "fig-003", "caption": "Figure 3: Plot.", "page": 1, "section": None,
        "image_path": "figures/fig-003.png", "bbox": [100.0, 200.0, 500.0, 400.0],
        "env_index": 2}]), encoding="utf-8")
    (latex / "source.html").write_text(
        '<p><span id="phfig-003"></span></p><figure><img src="a.png"></figure>',
        encoding="utf-8")
    pdfp = tmp_path / "cache" / "pdfp"
    _pdf(pdfp / "source.pdf", pages=3)
    bare = tmp_path / "cache" / "bare"
    bare.mkdir(parents=True)
    ids: dict[str, int] = {}
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        for name, root, kind in (("latex", latex, "arxiv"), ("pdf", pdfp, "pdf_upload"),
                                 ("bare", bare, "pdf_upload")):
            await conn.execute(
                "INSERT INTO paper_content (content_key, kind, arxiv_id, sha256, title, "
                "source_path, source_dir_path, html_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"k:{name}", kind, "2401.00001" if kind == "arxiv" else None,
                 None if kind == "arxiv" else name, name, "/x", str(root),
                 str(root / "source.html")))
            async with conn.execute("SELECT last_insert_rowid()") as cur:
                row = await cur.fetchone()
            assert row is not None
            ids[name] = int(row[0])
        await conn.commit()
    return ids


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


async def test_document_mode_reports_the_page_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _seed(tmp_path, monkeypatch)
    async with await _client() as client:
        latex = (await client.get(f"/papers/content/{ids['latex']}/document")).json()
        pdf = (await client.get(f"/papers/content/{ids['pdf']}/document")).json()
        bare = (await client.get(f"/papers/content/{ids['bare']}/document")).json()
    assert latex == {"mode": "html", "page_pdf": True}
    assert pdf == {"mode": "pdf", "page_pdf": True}
    assert bare == {"mode": "html", "page_pdf": False}


async def test_page_pdf_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = await _seed(tmp_path, monkeypatch)
    async with await _client() as client:
        ok = await client.get(f"/papers/content/{ids['latex']}/page-pdf")
        missing = await client.get(f"/papers/content/{ids['bare']}/page-pdf")
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/pdf"
    assert ok.content[:5] == b"%PDF-"
    assert missing.status_code == 404


async def test_figure_meta_carries_its_locators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _seed(tmp_path, monkeypatch)
    async with await _client() as client:
        meta = (await client.get(f"/papers/content/{ids['latex']}/figures/fig-003")).json()
    assert meta["html_anchor"] == "phfig-003"
    assert meta["page"] == 1 and meta["bbox"] == [100.0, 200.0, 500.0, 400.0]


async def test_view_region_in_pdf_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = await _seed(tmp_path, monkeypatch)
    async with await _client() as client:
        latex = await client.get(f"/papers/content/{ids['latex']}/view-region",
                                 params={"page": 2, "box": "100,250,600,500"})
        own = await client.get(f"/papers/content/{ids['pdf']}/view-region",
                               params={"page": 3, "box": "0,0,1000,1000"})
        bad_box = await client.get(f"/papers/content/{ids['pdf']}/view-region",
                                   params={"page": 1, "box": "5,5,1"})
        reversed_box = await client.get(f"/papers/content/{ids['pdf']}/view-region",
                                        params={"page": 1, "box": "500,5,100,9"})
        bad_page = await client.get(f"/papers/content/{ids['pdf']}/view-region",
                                    params={"page": 4, "box": "0,0,10,10"})
        none = await client.get(f"/papers/content/{ids['bare']}/view-region",
                                params={"page": 1, "box": "0,0,10,10"})
    assert latex.status_code == 200
    body = latex.json()
    assert body["page"] == 1 and body["source"] == "compiled"
    assert body["bbox"] == pytest.approx([61.2, 198.0, 367.2, 396.0])
    assert own.json() == {"page": 2, "bbox": [0.0, 0.0, 612.0, 792.0], "source": "own"}
    assert bad_box.status_code == 400 and reversed_box.status_code == 400
    assert bad_page.status_code == 404 and none.status_code == 404
