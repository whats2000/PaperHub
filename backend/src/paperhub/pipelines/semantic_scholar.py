"""Semantic Scholar REST client (SRS v2.4).

Public REST API, free tier (rate-limited to ~100 req / 5 min unauthenticated,
~1 req/s with PAPERHUB_SEMANTIC_SCHOLAR_API_KEY). No auth required for the
demo. See https://api.semanticscholar.org/api-docs/.

This module is the v2.4 primary external-search layer. It exposes:

- ``find_related`` — citation-graph navigation (uncapped, kept from v2.3).
- ``search_papers`` — free-text search across the ~200M paper corpus
  (replaces ``search_arxiv`` in the LLM-visible palette).
- ``fetch_paper_metadata`` — single-paper lookup used by the ``ss:`` ingest
  branch (chooses arXiv vs openAccessPdf fallback).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx

API_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = httpx.Timeout(10.0)
# arXiv asks for a contactable User-Agent per their Terms of Use; Semantic Scholar
# is more permissive but we send the same UA for operator visibility.
_USER_AGENT = "PaperHub/0.1 (https://github.com/whats2000/PaperHub)"
_FIELDS = "title,abstract,year,authors.name,externalIds"
# Extended fields used by search_papers / fetch_paper_metadata for the v2.4
# suggest-with-finalize flow (needs openAccessPdf.url + paperId).
_SEARCH_FIELDS = "paperId,title,abstract,year,authors.name,externalIds,openAccessPdf"

Mode = Literal["cites", "cited_by", "similar"]


class SemanticScholarRateLimitError(Exception):
    """Raised when Semantic Scholar returns HTTP 429 (rate limited)."""


@dataclass(frozen=True)
class RelatedPaper:
    """Semantic Scholar result coerced into PaperHub shape (v2.3 shape).

    ``arxiv_id`` may be None if the related paper isn't on arXiv —
    Plan C exposes but cannot ingest non-arxiv papers via this path.
    """

    title: str
    abstract: str
    year: int | None
    authors: list[str]
    arxiv_id: str | None  # extracted from externalIds.ArXiv when present


@dataclass(frozen=True)
class SemanticScholarHit:
    """Single hit from /graph/v1/paper/search."""

    paperId: str  # noqa: N815 — matches the SS field name
    title: str
    abstract: str | None
    year: int | None
    authors: list[str]
    arxiv_id: str | None  # extracted from externalIds.ArXiv
    open_access_pdf_url: str | None  # from openAccessPdf.url


@dataclass(frozen=True)
class SemanticScholarMetadata:
    """Same shape as a hit; separate type so callers can document intent
    (single-paper lookup vs. search-result list)."""

    paperId: str  # noqa: N815 — matches the SS field name
    title: str
    abstract: str | None
    year: int | None
    authors: list[str]
    arxiv_id: str | None
    open_access_pdf_url: str | None


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"User-Agent": _USER_AGENT}
    key = os.environ.get("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY")
    if key:
        h["x-api-key"] = key
    return h


def _coerce(item: dict[str, Any]) -> RelatedPaper:
    return RelatedPaper(
        title=item.get("title") or "",
        abstract=item.get("abstract") or "",
        year=item.get("year"),
        authors=[a["name"] for a in item.get("authors") or [] if a.get("name")],
        arxiv_id=(item.get("externalIds") or {}).get("ArXiv"),
    )


def _coerce_hit(item: dict[str, Any]) -> SemanticScholarHit:
    open_pdf = item.get("openAccessPdf") or {}
    return SemanticScholarHit(
        paperId=str(item.get("paperId") or ""),
        title=item.get("title") or "",
        abstract=item.get("abstract"),
        year=item.get("year"),
        authors=[a["name"] for a in item.get("authors") or [] if a.get("name")],
        arxiv_id=(item.get("externalIds") or {}).get("ArXiv"),
        open_access_pdf_url=open_pdf.get("url") if isinstance(open_pdf, dict) else None,
    )


def _coerce_metadata(item: dict[str, Any]) -> SemanticScholarMetadata:
    hit = _coerce_hit(item)
    return SemanticScholarMetadata(
        paperId=hit.paperId,
        title=hit.title,
        abstract=hit.abstract,
        year=hit.year,
        authors=hit.authors,
        arxiv_id=hit.arxiv_id,
        open_access_pdf_url=hit.open_access_pdf_url,
    )


async def find_related(
    arxiv_id: str,
    *,
    mode: Mode,
    max_results: int = 8,
) -> list[RelatedPaper]:
    """Return papers related to the given arXiv ID via Semantic Scholar.

    Caller is expected to wrap in tracer.step() — this helper is transport-only.
    """
    paper_id = f"arXiv:{arxiv_id}"
    sub_key: str | None
    if mode == "cites":
        url = f"{API_BASE}/paper/{paper_id}/references"
        items_key = "data"
        sub_key = "citedPaper"
    elif mode == "cited_by":
        url = f"{API_BASE}/paper/{paper_id}/citations"
        items_key = "data"
        sub_key = "citingPaper"
    else:  # similar
        url = f"{API_BASE}/paper/{paper_id}/related"
        items_key = "data"
        sub_key = None  # similar endpoint returns paper objects directly

    params = {"limit": str(max_results), "fields": _FIELDS}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params, headers=_headers())
    if resp.status_code == 429:
        raise SemanticScholarRateLimitError(
            f"Semantic Scholar rate-limited find_related({mode}) for {arxiv_id}",
        )
    resp.raise_for_status()
    raw = resp.json().get(items_key) or []
    items = [(r.get(sub_key) if sub_key else r) for r in raw]
    return [_coerce(i) for i in items if i]


async def search_papers(
    query: str, max_results: int = 8,
) -> list[SemanticScholarHit]:
    """Free-text search across Semantic Scholar's ~200M paper corpus.

    Returns hits with title, abstract, authors, year, plus ``arxiv_id``
    (when ``externalIds.ArXiv`` is present) and ``open_access_pdf_url``
    (when ``openAccessPdf.url`` is present) so the ``ss:`` ingest branch
    can choose the cleanest source.
    """
    url = f"{API_BASE}/paper/search"
    params = {"query": query, "limit": str(max_results), "fields": _SEARCH_FIELDS}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params, headers=_headers())
    if resp.status_code == 429:
        raise SemanticScholarRateLimitError(
            f"Semantic Scholar rate-limited search_papers({query!r})",
        )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    return [_coerce_hit(item) for item in data if item]


async def fetch_paper_metadata(paper_id: str) -> SemanticScholarMetadata:
    """Single-paper metadata lookup, used by the ``ss:<paperId>`` ingest
    branch to decide between the arXiv path and the openAccessPdf fallback.
    """
    url = f"{API_BASE}/paper/{paper_id}"
    params = {"fields": _SEARCH_FIELDS}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params, headers=_headers())
    if resp.status_code == 429:
        raise SemanticScholarRateLimitError(
            f"Semantic Scholar rate-limited fetch_paper_metadata({paper_id!r})",
        )
    resp.raise_for_status()
    return _coerce_metadata(resp.json())
