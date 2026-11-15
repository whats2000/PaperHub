"""An OA URL on a host the allowlist forbids falls to the manual-download card."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import httpx
import pytest
import pytest_asyncio
import respx

from paperhub.agents.research_tools import (
    NoIngestibleSourceError,
    add_paper_to_session_dispatch,
)
from paperhub.db.migrate import apply_schema
from paperhub.outbound.destinations import OutboundBlocked, pending_log_writes
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.pipelines.semantic_scholar import SemanticScholarMetadata
from paperhub.pipelines.unpaywall import UNPAYWALL_BASE, find_oa_pdf_urls_by_doi

pytestmark = pytest.mark.asyncio

_OA = "https://publisher.example/paper.pdf"


@pytest_asyncio.fixture
async def deployed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")
    monkeypatch.delenv("PAPERHUB_OUTBOUND_ALLOWLIST", raising=False)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as c:
        await apply_schema(c)
    return tmp_path


async def _blocked(ws: Path) -> list[str]:
    await asyncio.gather(*pending_log_writes())  # off-loop block-row writes
    with sqlite3.connect(ws / "paperhub.db") as c:
        return [r[0] for r in c.execute(
            "SELECT destination FROM outbound_log WHERE decision='blocked'")]


def _meta() -> SemanticScholarMetadata:
    return SemanticScholarMetadata(
        paperId="p", title="T", abstract="a", year=2025, authors=["A"],
        arxiv_id=None, open_access_pdf_url=_OA, doi=None)


async def test_oa_download_blocked_host_gives_manual_card(
    deployed: Path, monkeypatch: pytest.MonkeyPatch,
    migrated_db: aiosqlite.Connection,
) -> None:
    async def fake_meta(_id: str) -> SemanticScholarMetadata:
        return _meta()

    monkeypatch.setattr("paperhub.agents.research_tools.fetch_paper_metadata", fake_meta)
    pipeline = MagicMock(spec=PaperPipeline)
    pipeline.ingest_pdf_from_url = AsyncMock()
    with pytest.raises(NoIngestibleSourceError) as ei:  # the existing manual card
        await add_paper_to_session_dispatch(
            "ss:p", pipeline=pipeline, conn=migrated_db, session_id=1)
    assert ei.value.tried_urls == [_OA]
    pipeline.ingest_pdf_from_url.assert_not_called()
    assert await _blocked(deployed) == ["publisher.example"]


@respx.mock
async def test_unpaywall_blocked_host_is_skipped(
    deployed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unpaywall is on the deployment default list; an operator list without it blocks.
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "arxiv.org")
    route = respx.get(f"{UNPAYWALL_BASE}/10.1/x").mock(
        return_value=httpx.Response(200, json={}))
    assert await find_oa_pdf_urls_by_doi("10.1/x", email="a@b.c") == []
    assert not route.called
    assert await _blocked(deployed) == ["api.unpaywall.org"]


@respx.mock
async def test_ingest_pdf_blocks_redirect_to_unlisted_host(
    deployed: Path, monkeypatch: pytest.MonkeyPatch,
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "good.example")
    respx.get("https://good.example/a.pdf").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/a.pdf"}))
    evil = respx.get("https://evil.example/a.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF"))
    pipeline = PaperPipeline(migrated_db, papers_cache_dir=tmp_path / "cache")
    with pytest.raises(OutboundBlocked):
        await pipeline.ingest_pdf_from_url(
            session_id=1, pdf_url="https://good.example/a.pdf", title_hint="t",
            abstract_hint="", authors_hint=[], year_hint=None)
    assert not evil.called
    assert "evil.example" in await _blocked(deployed)
