"""Tests for Semantic Scholar REST client (SRS v2.3)."""
from __future__ import annotations

import httpx
import pytest
import respx

from paperhub.pipelines import semantic_scholar as ss
from paperhub.pipelines.semantic_scholar import (
    API_BASE,
    SemanticScholarRateLimitError,
    fetch_paper_metadata,
    find_related,
    search_papers,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fast_ss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the rate-limiter instant in tests: no pacing delay, no real
    backoff sleeps. Records sleep durations on a list for pacing assertions."""
    monkeypatch.setattr(ss, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(ss, "_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(ss, "_last_request_ts", 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(ss, "_sleep", _no_sleep)


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
    assert hits[0].doi == "10.x/y"
    assert hits[1].arxiv_id is None
    assert hits[1].open_access_pdf_url == "https://example.org/x.pdf"
    assert hits[1].doi == "10.x/z"


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
async def test_fetch_paper_metadata_no_doi() -> None:
    """A paper whose externalIds has no DOI key must yield meta.doi is None."""
    respx.get(f"{API_BASE}/paper/arxiv-only").mock(
        return_value=httpx.Response(
            200,
            json={
                "paperId": "arxiv-only",
                "title": "ArXiv-only Paper",
                "abstract": "no doi here",
                "year": 2025,
                "authors": [{"name": "Dave"}],
                "externalIds": {"ArXiv": "2501.00001"},
                "openAccessPdf": None,
            },
        ),
    )
    meta = await fetch_paper_metadata("arxiv-only")
    assert meta.doi is None
    assert meta.arxiv_id == "2501.00001"  # coerce path completed, ArXiv still extracted


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


# ---------------------------------------------------------------------------
# Rate-limit resilience: retry-on-429 + paced concurrency
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_papers_retries_transient_429_then_succeeds() -> None:
    """A 429 that clears on a later attempt must NOT surface as an error —
    the client retries (with backoff) and returns the eventual 200 payload.
    This is the fix for the prod 'rate-limited → all results not found' bug."""
    route = respx.get(f"{API_BASE}/paper/search")
    route.side_effect = [
        httpx.Response(429, json={"message": "rate limited"}),
        httpx.Response(429, json={"message": "rate limited"}),
        httpx.Response(200, json={"data": [_SS_PAPER_WITH_ARXIV]}),
    ]
    hits = await search_papers("mamba", max_results=5)
    assert len(hits) == 1
    assert hits[0].arxiv_id == "2312.00752"
    assert route.call_count == 3  # two 429s retried, third succeeded


@respx.mock
async def test_search_papers_raises_only_after_exhausting_retries() -> None:
    """A 429 on EVERY attempt still raises — but only after _MAX_ATTEMPTS."""
    route = respx.get(f"{API_BASE}/paper/search").mock(
        return_value=httpx.Response(429, json={"message": "rate limited"}),
    )
    with pytest.raises(SemanticScholarRateLimitError):
        await search_papers("q")
    assert route.call_count == ss._MAX_ATTEMPTS


@respx.mock
async def test_concurrent_calls_are_paced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent SS callers must be spaced ≥ _MIN_INTERVAL_S apart (the
    'schedule parallel search with delay' lever) so a fan-out of resolves
    doesn't burst-trip the rate limit. We assert the second call sleeps for
    the remaining interval using a controlled clock + recorded sleeps."""
    monkeypatch.setattr(ss, "_MIN_INTERVAL_S", 1.0)
    monkeypatch.setattr(ss, "_last_request_ts", 100.0)
    # Fake clock: 0.2s have elapsed since the last request → 0.8s still owed.
    monkeypatch.setattr(ss.time, "monotonic", lambda: 100.2)
    slept: list[float] = []

    async def _rec(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(ss, "_sleep", _rec)
    respx.get(f"{API_BASE}/paper/search").mock(
        return_value=httpx.Response(200, json={"data": []}),
    )
    await search_papers("q")
    assert slept, "expected a pacing sleep before issuing the request"
    assert abs(slept[0] - 0.8) < 1e-6
