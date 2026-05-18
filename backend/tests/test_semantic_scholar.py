"""Tests for Semantic Scholar REST client (SRS v2.3)."""
from __future__ import annotations

import httpx
import pytest
import respx

from paperhub.pipelines.semantic_scholar import (
    API_BASE,
    SemanticScholarRateLimitError,
    fetch_paper_metadata,
    find_related,
    search_papers,
)

pytestmark = pytest.mark.asyncio


_PAPER_WITH_ARXIV = {
    "title": "Some Paper",
    "abstract": "abs",
    "year": 2024,
    "authors": [{"name": "Alice"}, {"name": "Bob"}],
    "externalIds": {"ArXiv": "2403.00001", "DOI": "10.x/y"},
}

_PAPER_NO_ARXIV = {
    "title": "Non-arXiv Paper",
    "abstract": "abs2",
    "year": 2023,
    "authors": [{"name": "Carol"}],
    "externalIds": {"DOI": "10.x/z"},
}


@respx.mock
async def test_find_related_cites() -> None:
    """mode=cites hits /references and unwraps citedPaper."""
    arxiv_id = "2402.12345"
    respx.get(f"{API_BASE}/paper/arXiv:{arxiv_id}/references").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"citedPaper": _PAPER_WITH_ARXIV},
                    {"citedPaper": _PAPER_NO_ARXIV},
                ],
            },
        ),
    )
    result = await find_related(arxiv_id, mode="cites", max_results=5)
    assert len(result) == 2
    assert result[0].arxiv_id == "2403.00001"
    assert result[0].title == "Some Paper"
    assert result[0].authors == ["Alice", "Bob"]
    # arxiv_id is None when externalIds lacks ArXiv key
    assert result[1].arxiv_id is None
    assert result[1].title == "Non-arXiv Paper"


@respx.mock
async def test_find_related_cited_by() -> None:
    """mode=cited_by hits /citations and unwraps citingPaper."""
    arxiv_id = "2402.12345"
    respx.get(f"{API_BASE}/paper/arXiv:{arxiv_id}/citations").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"citingPaper": _PAPER_WITH_ARXIV}]},
        ),
    )
    result = await find_related(arxiv_id, mode="cited_by", max_results=8)
    assert len(result) == 1
    assert result[0].arxiv_id == "2403.00001"


@respx.mock
async def test_find_related_similar() -> None:
    """mode=similar hits /related and reads paper objects directly (no sub-key)."""
    arxiv_id = "2402.12345"
    respx.get(f"{API_BASE}/paper/arXiv:{arxiv_id}/related").mock(
        return_value=httpx.Response(
            200, json={"data": [_PAPER_WITH_ARXIV, _PAPER_NO_ARXIV]},
        ),
    )
    result = await find_related(arxiv_id, mode="similar", max_results=8)
    assert len(result) == 2
    assert result[0].arxiv_id == "2403.00001"
    assert result[1].arxiv_id is None


# ---------------------------------------------------------------------------
# v2.4-5: search_papers + fetch_paper_metadata
# ---------------------------------------------------------------------------


_SS_PAPER_WITH_ARXIV = {
    "paperId": "abcd1234",
    "title": "Mamba",
    "abstract": "linear-time state space",
    "year": 2024,
    "authors": [{"name": "Alice"}, {"name": "Bob"}],
    "externalIds": {"ArXiv": "2312.00752", "DOI": "10.x/y"},
    "openAccessPdf": {"url": "https://arxiv.org/pdf/2312.00752"},
}

_SS_PAPER_NO_ARXIV_WITH_PDF = {
    "paperId": "efgh5678",
    "title": "Some non-arxiv paper",
    "abstract": "abs",
    "year": 2023,
    "authors": [{"name": "Carol"}],
    "externalIds": {"DOI": "10.x/z"},
    "openAccessPdf": {"url": "https://example.org/x.pdf"},
}

_SS_PAPER_NO_SOURCE = {
    "paperId": "ijkl9012",
    "title": "Closed-access paper",
    "abstract": "abs",
    "year": 2022,
    "authors": [],
    "externalIds": {"DOI": "10.x/w"},
    "openAccessPdf": None,
}


@respx.mock
async def test_search_papers_extracts_externalIds_arxiv() -> None:
    respx.get(f"{API_BASE}/paper/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _SS_PAPER_WITH_ARXIV,
                    _SS_PAPER_NO_ARXIV_WITH_PDF,
                ],
            },
        ),
    )
    hits = await search_papers("mamba state space", max_results=5)
    assert len(hits) == 2
    assert hits[0].paperId == "abcd1234"
    assert hits[0].arxiv_id == "2312.00752"
    assert hits[0].open_access_pdf_url == "https://arxiv.org/pdf/2312.00752"
    assert hits[0].authors == ["Alice", "Bob"]
    assert hits[1].arxiv_id is None
    assert hits[1].open_access_pdf_url == "https://example.org/x.pdf"


@respx.mock
async def test_fetch_paper_metadata_extracts_pdf_url() -> None:
    respx.get(f"{API_BASE}/paper/efgh5678").mock(
        return_value=httpx.Response(200, json=_SS_PAPER_NO_ARXIV_WITH_PDF),
    )
    meta = await fetch_paper_metadata("efgh5678")
    assert meta.paperId == "efgh5678"
    assert meta.arxiv_id is None
    assert meta.open_access_pdf_url == "https://example.org/x.pdf"
    assert meta.title == "Some non-arxiv paper"


@respx.mock
async def test_fetch_paper_metadata_no_source() -> None:
    respx.get(f"{API_BASE}/paper/ijkl9012").mock(
        return_value=httpx.Response(200, json=_SS_PAPER_NO_SOURCE),
    )
    meta = await fetch_paper_metadata("ijkl9012")
    assert meta.arxiv_id is None
    assert meta.open_access_pdf_url is None


@respx.mock
async def test_search_papers_handles_429() -> None:
    respx.get(f"{API_BASE}/paper/search").mock(
        return_value=httpx.Response(429, json={"message": "rate limited"}),
    )
    with pytest.raises(SemanticScholarRateLimitError):
        await search_papers("q")


@respx.mock
async def test_fetch_paper_metadata_handles_429() -> None:
    respx.get(f"{API_BASE}/paper/abcd").mock(
        return_value=httpx.Response(429, json={"message": "rate limited"}),
    )
    with pytest.raises(SemanticScholarRateLimitError):
        await fetch_paper_metadata("abcd")
