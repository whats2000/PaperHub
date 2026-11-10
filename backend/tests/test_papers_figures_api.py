"""GET /papers/content/{id}/figures/{figure_id}[/image]: a cited figure's
caption, image and locators for the ``[fig:P/ID]`` citation."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


async def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    src = tmp_path / "cache" / "p"
    (src / "asset" / "figures").mkdir(parents=True)
    Image.new("RGB", (30, 20), (0, 120, 0)).save(src / "asset" / "figures" / "fig-003.png")
    (src / "asset" / "figures.json").write_text(json.dumps([
        {"id": "fig-003", "caption": "Fig. 3 | Bar charts of AP", "page": 4,
         "section": None, "image_path": "figures/fig-003.png"},
        {"id": "fig-004", "caption": "Lost image", "page": 5,
         "section": None, "image_path": "figures/fig-004.png"},
    ]), encoding="utf-8")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
            "source_dir_path, html_path) VALUES ('sha256:p', 'pdf_upload', 'p', 'P', "
            "'/x.pdf', ?, '/x')", (str(src),))
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_figure_meta_and_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = await _seed(tmp_path, monkeypatch)
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        meta = await client.get(f"/papers/content/{pid}/figures/fig-003")
        image = await client.get(f"/papers/content/{pid}/figures/fig-003/image")
        missing_file = await client.get(f"/papers/content/{pid}/figures/fig-004/image")
        unknown = await client.get(f"/papers/content/{pid}/figures/fig-999")
        traversal = await client.get(f"/papers/content/{pid}/figures/..%2Fpaperhub.db/image")
        no_paper = await client.get(f"/papers/content/{pid + 1}/figures/fig-003")
    assert meta.status_code == 200
    assert meta.json() == {"paper_content_id": pid, "id": "fig-003",
                           "caption": "Fig. 3 | Bar charts of AP", "page": 4,
                           "title": "P", "has_image": True,
                           "html_anchor": None, "bbox": None}
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert missing_file.status_code == 404
    assert unknown.status_code == 404
    assert traversal.status_code == 404
    assert no_paper.status_code == 404
