"""Tests for Semantic Scholar REST client (SRS v2.3)."""
from __future__ import annotations

import httpx
import pytest
import respx

from paperhub.pipelines.semantic_scholar import API_BASE, find_related

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
