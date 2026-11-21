"""Read-only paper lookups for a note's ``[[`` / ``@`` links: a paper's
current title, and the chunk range of one of its sections."""
from __future__ import annotations

from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"X-Paperhub-Session-Id": "1"}


async def _seed(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, title, source_path, "
        "source_dir_path, html_path, arxiv_id) VALUES "
        "(7, 'arxiv:p7', 'arxiv', 'Attention Is All You Need', '/x/s.tex', "
        "'/x', '/x/s.html', 'p7')"
    )
    await conn.executemany(
        "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, "
        "text, dom_id, match_text, page, bbox) VALUES (?, 7, ?, ?, ?, 'x', NULL, "
        "NULL, NULL, NULL)",
        [
            (401, "1 Introduction", 0, 50),
            (402, "1 Introduction", 50, 100),
            (403, "2 Method", 100, 150),
            (404, "1 Introduction", 150, 160),  # out of order: still in range
            (405, "2 Method", 160, 200),
        ],
    )
    await conn.commit()


async def _get(app: Any, path: str, **params: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path, params=params, headers=_HDR)


@pytest.mark.asyncio
async def test_info_gives_the_current_title(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    app, conn = app_with_db
    await _seed(conn)
    resp = await _get(app, "/papers/content/7/info")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": 7, "title": "Attention Is All You Need"}


@pytest.mark.asyncio
async def test_info_of_a_removed_paper_is_404(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    app, _ = app_with_db
    resp = await _get(app, "/papers/content/99/info")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_section_range_is_first_and_last_chunk_in_document_order(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    app, conn = app_with_db
    await _seed(conn)
    resp = await _get(app, "/papers/content/7/section-range", section="1 Introduction")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"first_chunk_id": 401, "last_chunk_id": 404}
    resp = await _get(app, "/papers/content/7/section-range", section="2 Method")
    assert resp.json() == {"first_chunk_id": 403, "last_chunk_id": 405}


@pytest.mark.asyncio
async def test_section_range_of_an_unknown_section_is_404(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    app, conn = app_with_db
    await _seed(conn)
    resp = await _get(app, "/papers/content/7/section-range", section="9 Nowhere")
    assert resp.status_code == 404
    resp = await _get(app, "/papers/content/99/section-range", section="1 Introduction")
    assert resp.status_code == 404
