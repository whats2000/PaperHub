import json
import struct
from pathlib import Path

import aiosqlite
import pymupdf
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.pipelines.paper_asset import BlockAsset, write_block_assets

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    src = tmp_path / "paper"
    src.mkdir()
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    doc.new_page(width=600, height=800).insert_text((72, 100), "hello")
    doc.new_page(width=600, height=800)
    doc.save(src / "paper.pdf")
    write_block_assets(src, [
        BlockAsset("b1", "Text", 0, (60.0, 80.0, 300.0, 120.0),
                   "<p>Hello <b>&amp;</b> world</p>", "Intro"),
        BlockAsset("b2", "Text", 1, (0.0, 0.0, 600.0, 400.0), "<p>p2</p>", None),
        BlockAsset("b3", "Text", 0, (0.0, 0.0, 0.0, 0.0), "<p>nogeo</p>", None),
    ], inline_math_llm=True)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, sha256, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (1, 'pdf:1', 'pdf_upload', NULL, 'sha1', 'P', ?, ?, 'h')",
            (str(src / "paper.pdf"), str(src)))
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, sha256, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (2, 'pdf:2', 'pdf_upload', NULL, 'sha2', 'Q', 's', ?, 'h')",
            (str(tmp_path / "empty"),))
        await conn.commit()
    (tmp_path / "empty").mkdir()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


def _size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])  # type: ignore[return-value]


async def test_page_crop_png_bounds(client: AsyncClient) -> None:
    r = await client.get("/papers/content/1/page-crop", params={
        "page": 0, "x0": 0.1, "y0": 0.1, "x1": 0.6, "y1": 0.3})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert _size(r.content) == (600, 320)  # 300x160 pt at 2x
    big = await client.get("/papers/content/1/page-crop", params={
        "page": 0, "x0": 0, "y0": 0, "x1": 1, "y1": 1})
    w, h = _size(big.content)
    assert max(w, h) <= 1600 and h == 1600  # 600x800pt would be 1200x1600 at 2x
    bad = {"page": 0, "x0": 0.5, "y0": 0.1, "x1": 0.4, "y1": 0.3}
    assert (await client.get("/papers/content/1/page-crop", params=bad)
            ).status_code == 400
    assert (await client.get("/papers/content/1/page-crop", params={
        "page": 5, "x0": 0, "y0": 0, "x1": 1, "y1": 1})).status_code == 404
    assert (await client.get("/papers/content/2/page-crop", params={
        "page": 0, "x0": 0, "y0": 0, "x1": 1, "y1": 1})).status_code == 404


async def test_blocks_endpoint_fractions(client: AsyncClient) -> None:
    r = await client.get("/papers/content/1/blocks", params={"page": 0})
    assert r.status_code == 200
    [b] = r.json()  # b3 has no geometry and is left out
    assert b["block_id"] == "b1" and b["type"] == "Text" and b["page"] == 0
    assert b["text"] == "Hello & world"
    assert b["box"] == pytest.approx([0.1, 0.1, 0.5, 0.15])
    p2 = (await client.get("/papers/content/1/blocks", params={"page": 1})).json()
    assert p2[0]["box"] == pytest.approx([0.0, 0.0, 1.0, 0.5])
    assert (await client.get("/papers/content/1/blocks",
                             params={"page": 7})).json() == []
    assert (await client.get("/papers/content/2/blocks",
                             params={"page": 0})).json() == []
    assert json.dumps(p2)  # serialisable


async def test_page_crop_sub_pixel_box_is_400(client: AsyncClient) -> None:
    r = await client.get("/papers/content/1/page-crop", params={
        "page": 0, "x0": 0.5, "y0": 0.5, "x1": 0.5001, "y1": 0.9})
    assert r.status_code == 400
