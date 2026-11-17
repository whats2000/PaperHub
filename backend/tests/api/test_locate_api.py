"""POST /papers/content/{id}/locate — where text read off the LaTeX-compiled
PDF of an HTML paper sits in its reading text (H5b.1 PDF marks, Follow)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"X-Paperhub-Session-Id": "1"}


@pytest.mark.asyncio
async def test_locate_returns_the_match_and_why(
    app_with_db: tuple[Any, aiosqlite.Connection], tmp_path: Path,
) -> None:
    app, conn = app_with_db
    html = tmp_path / "source.html"
    html.write_text(
        '<html><body><p><span id="phchunk-0"></span>Robots learn from demonstrations.</p>'
        '<p><span id="phchunk-1"></span>We predict the agent pose ahead of time.</p>'
        "</body></html>", encoding="utf-8")
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, title, source_path, "
        "source_dir_path, html_path, arxiv_id) VALUES "
        "(7, 'arxiv:p7', 'arxiv', 'P7', '/x/s.tex', '/x', ?, 'p7')", (str(html),))
    await conn.executemany(
        "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, "
        "text, dom_id) VALUES (?, 7, ?, 0, 1, 'x', ?)",
        [(401, "1 Introduction", "phchunk-0"), (402, "2 Method", "phchunk-1")])
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hit = await client.post("/papers/content/7/locate", headers=_HDR,
                                json={"text": "We predict the agent pose ahead"})
        miss = await client.post("/papers/content/7/locate", headers=_HDR,
                                 json={"text": "Nothing like this is in the paper"})
    assert hit.status_code == 200, hit.text
    body = hit.json()
    assert body["match"]["section"] == "2 Method"
    assert (body["match"]["chunk_start"], body["match"]["chunk_end"]) == (402, 402)
    assert body["match"]["chunk_ids"] == [402]
    # The one reading-text paragraph it sits in (the highlighter joins by it).
    assert isinstance(body["match"]["paragraph"], int)
    assert body["_meta"]["matched"] is True and body["_meta"]["source"] == "html_text"
    assert miss.json()["match"] is None
    assert miss.json()["_meta"]["reason"] == "no_start"
