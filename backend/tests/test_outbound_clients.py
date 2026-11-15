"""The destination allowlist at the arXiv / Semantic Scholar clients."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import httpx
import pytest
import pytest_asyncio
import respx

from paperhub.db.migrate import apply_schema
from paperhub.outbound.destinations import (
    OutboundBlocked,
    check_destination,
    pending_log_writes,
)
from paperhub.pipelines import arxiv_client, semantic_scholar


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow: str) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", allow)

    async def init() -> None:
        async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
            await apply_schema(c)

    asyncio.run(init())


def _blocked(ws: Path) -> list[str]:
    with sqlite3.connect(ws / "paperhub.db") as c:
        return [r[0] for r in c.execute(
            "SELECT destination FROM outbound_log WHERE decision='blocked'")]


@pytest.fixture
def sync_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _setup(tmp_path, monkeypatch, "good.example")
    return tmp_path


def test_download_with_resume_raises_on_unlisted_host(sync_ws: Path) -> None:
    with pytest.raises(OutboundBlocked):
        arxiv_client._download_with_resume("https://export.arxiv.org/pdf/1", sync_ws / "x.pdf")


def test_fetch_arxiv_by_ids_returns_none_when_blocked(sync_ws: Path) -> None:
    assert arxiv_client.fetch_arxiv_by_ids(["2510.21771"]) is None
    assert arxiv_client.fetch_arxiv_by_id("2510.21771") is None


@respx.mock
def test_download_redirect_to_unlisted_host_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, "export.arxiv.org")
    respx.get("https://export.arxiv.org/pdf/1").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/p.pdf"}))
    evil = respx.get("https://evil.example/p.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF"))
    with pytest.raises(OutboundBlocked):
        arxiv_client._download_with_resume("https://export.arxiv.org/pdf/1", tmp_path / "x.pdf")
    assert not evil.called
    assert _blocked(tmp_path) == ["evil.example"]


@respx.mock
def test_download_redirect_between_allowed_hosts_is_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, "export.arxiv.org,arxiv.org")
    respx.get("https://export.arxiv.org/pdf/1").mock(
        return_value=httpx.Response(301, headers={"location": "/pdf/1v2"}))
    respx.get("https://export.arxiv.org/pdf/1v2").mock(
        return_value=httpx.Response(302, headers={"location": "https://arxiv.org/pdf/1v2"}))
    respx.get("https://arxiv.org/pdf/1v2").mock(
        return_value=httpx.Response(200, content=b"%PDF-ok"))
    target = tmp_path / "x.pdf"
    arxiv_client._download_with_resume("https://export.arxiv.org/pdf/1", target)
    assert target.read_bytes() == b"%PDF-ok"


@pytest_asyncio.fixture
async def async_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "good.example")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        await apply_schema(c)
    return tmp_path


@pytest.mark.asyncio
async def test_s2_gate_is_in_the_shared_request_helper(async_ws: Path) -> None:
    """Any S2 call, even one a future function adds, goes through the gate."""
    with pytest.raises(OutboundBlocked):
        await semantic_scholar._get_with_retry(
            "https://api.semanticscholar.org/graph/v1/paper/x", {}, {},
            semantic_scholar._TIMEOUT, caller="t.future", kind="s2_lookup",
        )
    with pytest.raises(OutboundBlocked):
        await semantic_scholar.search_papers("q")
    with pytest.raises(OutboundBlocked):
        await semantic_scholar.fetch_paper_metadata("abc")
    await asyncio.gather(*pending_log_writes())
    assert _blocked(async_ws).count("api.semanticscholar.org") == 3


@pytest.mark.asyncio
async def test_block_row_is_written_off_the_loop_and_sync_path_too(async_ws: Path) -> None:
    with pytest.raises(OutboundBlocked):
        check_destination("https://evil.example/x", caller="c", kind="k")
    # Raised synchronously; the row lands once the executor write finishes.
    await asyncio.gather(*pending_log_writes())
    assert _blocked(async_ws) == ["evil.example"]
    assert pending_log_writes() == []
