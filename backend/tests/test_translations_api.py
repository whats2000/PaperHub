"""Tests for the translation REST surface (Plan H1, Task 5)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.translation.models import (
    TranslateRequest,
    TranslateResponse,
    TranslateResult,
)


class _StubService:
    def __init__(self) -> None:
        self.calls: list[TranslateRequest] = []

    async def translate(
        self, conn: aiosqlite.Connection, req: TranslateRequest,
    ) -> TranslateResponse:
        self.calls.append(req)
        return TranslateResponse(results=[
            TranslateResult(key=b.key, status="ok", text=f"T:{b.text}", model="m")
            for b in req.blocks
        ])


async def _seed(tmp_path: Path) -> int:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, title, authors_json, arxiv_id, "
            "source_path, source_dir_path, html_path) VALUES ('k', 'arxiv', 't', '[]', '2401.00001', 's', 'd', 'h')",
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_post_round_trips_through_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    pcid = await _seed(tmp_path)
    app = create_app()
    stub = _StubService()
    app.state.translation_service = stub
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/translations", json={
            "paper_content_id": pcid, "language": "ja",
            "blocks": [{"key": "h:0", "text": "hello"}],
        })
    assert r.status_code == 200
    assert r.json()["results"][0] == {
        "key": "h:0", "status": "ok", "text": "T:hello", "model": "m",
        "stale_terminology": False,
    }
    assert stub.calls[0].language == "ja"


@pytest.mark.asyncio
async def test_unknown_paper_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    await _seed(tmp_path)
    app = create_app()
    stub = _StubService()
    app.state.translation_service = stub
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/translations", json={
            "paper_content_id": 9999, "language": "ja",
            "blocks": [{"key": "h:0", "text": "x"}],
        })
    assert r.status_code == 404
    assert stub.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("n_blocks", [0, 6])
async def test_block_count_outside_1_to_5_is_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n_blocks: int,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    pcid = await _seed(tmp_path)
    app = create_app()
    stub = _StubService()
    app.state.translation_service = stub
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/translations", json={
            "paper_content_id": pcid, "language": "ja",
            "blocks": [{"key": f"h:{i}", "text": "x"} for i in range(n_blocks)],
        })
    assert r.status_code == 422
    assert stub.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("env", "expected"), [("1", True), ("0", False)])
async def test_config_reflects_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str, expected: bool,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_TRANSLATE_FULL_PAPER", env)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/translations/config")
    assert r.status_code == 200
    assert r.json() == {"full_paper": expected}


def test_default_service_follows_runtime_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperhub.api.translations import get_translation_service

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_TRANSLATE_MODEL", "a/one")
    app = create_app()

    class _Req:
        pass

    req: Any = _Req()
    req.app = app
    s1 = get_translation_service(req)
    assert get_translation_service(req) is s1
    monkeypatch.setenv("PAPERHUB_TRANSLATE_MODEL", "a/two")
    s2 = get_translation_service(req)
    assert s2 is not s1


@pytest.mark.asyncio
@pytest.mark.parametrize("body_blocks, language", [
    ([{"key": "h:0", "text": "a"}, {"key": "h:0", "text": "b"}], "ja"),
    ([{"key": "h:0", "text": "a"}], "   "),
    ([{"key": "", "text": "a"}], "ja"),
    ([{"key": "h:0", "text": "  "}], "ja"),
])
async def test_invalid_blocks_are_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    body_blocks: list[dict[str, str]], language: str,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    pcid = await _seed(tmp_path)
    app = create_app()
    stub = _StubService()
    app.state.translation_service = stub
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/translations", json={
            "paper_content_id": pcid, "language": language, "blocks": body_blocks,
        })
    assert r.status_code == 422
    assert stub.calls == []
