"""GET /papers/content/{id}/chunk-anchors — the reader's map from a
``phchunk-N`` span to its chunk id and section (H5b.1 marks)."""
from __future__ import annotations

from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"X-Paperhub-Session-Id": "1"}


@pytest.mark.asyncio
async def test_chunk_anchors_map_dom_ids_to_chunks(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    app, conn = app_with_db
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, title, source_path, "
        "source_dir_path, html_path, arxiv_id) VALUES "
        "(7, 'arxiv:p7', 'arxiv', 'P7', '/x/s.tex', '/x', '/x/s.html', 'p7')"
    )
    await conn.executemany(
        "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, "
        "text, dom_id, match_text, page, bbox) VALUES (?, 7, ?, ?, ?, 'x', ?, "
        "NULL, NULL, NULL)",
        [
            (402, "Method", 50, 100, "phchunk-1"),
            (401, "Introduction", 0, 50, "phchunk-0"),
            (403, "Method", 100, 150, None),  # sentinel lost: no span
            (404, None, 150, 160, "phchunk-3"),
        ],
    )
    await conn.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/papers/content/7/chunk-anchors", headers=_HDR)
        missing = await client.get("/papers/content/99/chunk-anchors", headers=_HDR)
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"id": 401, "dom_id": "phchunk-0", "section": "Introduction"},
        {"id": 402, "dom_id": "phchunk-1", "section": "Method"},
        {"id": 404, "dom_id": "phchunk-3", "section": None},
    ]
    assert missing.status_code == 200
    assert missing.json() == []
