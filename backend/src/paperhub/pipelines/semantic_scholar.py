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

import asyncio
import os
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx

from paperhub.pipelines.author_names import clean_author_names

API_BASE = "https://api.semanticscholar.org/graph/v1"
# The Graph API has no "similar papers" endpoint (/paper/<id>/related 404s);
# similar papers come from the Recommendations API. Its default pool is
# "recent" (only the last few months), which returns [] for most papers, so
# find_related asks for the "all-cs" pool.
RECOMMENDATIONS_BASE = "https://api.semanticscholar.org/recommendations/v1"
_TIMEOUT = httpx.Timeout(10.0)

# Rate-limit resilience (observed in prod traces — paper_suggest fans out 4
# resolves concurrently and the free tier, even WITH an api-key, throttles
# to ~1 req/s → HTTP 429 → the resolver dropped real papers as "not found").
# Two levers, both tunable via env:
#   1. PACING — every SS request serializes through `_pace_lock` and is spaced
#      at least `_MIN_INTERVAL_S` apart, so concurrent callers queue instead of
#      bursting. This is the "schedule parallel search with delay" lever.
#   2. RETRY — a 429 is retried up to `_MAX_ATTEMPTS` times with exponential
#      backoff (honouring `Retry-After` when present). Only a 429 that survives
#      every attempt raises SemanticScholarRateLimitError.
_MIN_INTERVAL_S = float(os.environ.get("PAPERHUB_SS_MIN_INTERVAL_S", "1.1"))
_MAX_ATTEMPTS = int(os.environ.get("PAPERHUB_SS_MAX_ATTEMPTS", "4"))
_RETRY_BASE_S = float(os.environ.get("PAPERHUB_SS_RETRY_BASE_S", "1.0"))
# Module-level so pacing is shared across ALL concurrent SS calls in the
# worker (in-process MCP runs in the same event loop). Created without a
# running loop — fine on 3.10+ (binds lazily).
_pace_lock = asyncio.Lock()
_last_request_ts = 0.0
# Indirection so tests can monkeypatch sleep without touching global asyncio.
_sleep = asyncio.sleep


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (integer seconds OR HTTP-date) into a
    wait in seconds. Returns None when absent / unparseable."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (when - datetime.now(UTC)).total_seconds()
    return max(delta, 0.0)


async def _get_with_retry(
    url: str, params: dict[str, str], headers: dict[str, str],
    timeout_cfg: httpx.Timeout,
    *,
    attempts: list[int] | None = None,
) -> httpx.Response:
    """GET ``url`` through the shared pacing lock, retrying on HTTP 429.

    Returns the final response (which may itself be a 429 after attempts are
    exhausted — the caller raises SemanticScholarRateLimitError on that).
    Spacing + the request happen inside ``_pace_lock`` so concurrent callers
    are serialized to ~1 req/s; the backoff sleep is OUTSIDE the lock so a
    retrying caller doesn't block others.
    """
    global _last_request_ts
    resp: httpx.Response | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        async with _pace_lock:
            wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request_ts)
            if wait > 0:
                await _sleep(wait)
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                resp = await client.get(url, params=params, headers=headers)
            _last_request_ts = time.monotonic()
        if resp.status_code != 429 or attempt == _MAX_ATTEMPTS:
            if attempts is not None:
                attempts[:] = [attempt]
            return resp
        retry_after = _parse_retry_after(resp.headers.get("retry-after"))
        backoff = (
            retry_after if retry_after is not None
            else _RETRY_BASE_S * (2 ** (attempt - 1))
        )
        backoff += random.uniform(0, 0.3)
        await _sleep(backoff)
    assert resp is not None  # loop runs ≥1 time (_MAX_ATTEMPTS ≥ 1)
    return resp
async def _get_checked(
    url: str, params: dict[str, str], diag: dict[str, Any] | None, label: str,
) -> httpx.Response:
    """GET with pacing + 429 retry, fill ``diag`` with the transport facts
    (``url_path``, ``http_status``, ``attempts``, ``retry_after``, plus
    ``body_head`` on a non-2xx) and raise on failure: a final 429 as
    SemanticScholarRateLimitError, any other non-2xx as HTTPStatusError whose
    message carries the body head (S2 explains a 400 there)."""
    attempts: list[int] = []
    resp = await _get_with_retry(url, params, _headers(), _TIMEOUT, attempts=attempts)
    if diag is not None:
        diag.update({
            "url_path": httpx.URL(url).path,
            "http_status": resp.status_code,
            "attempts": attempts[0] if attempts else 1,
            "retry_after": resp.headers.get("retry-after"),
        })
    if resp.status_code == 429:
        raise SemanticScholarRateLimitError(f"Semantic Scholar rate-limited {label}")
    if resp.is_error:
        body_head = resp.text[:500]
        if diag is not None:
            diag["body_head"] = body_head
        raise httpx.HTTPStatusError(
            f"Semantic Scholar {label} -> HTTP {resp.status_code}: {body_head}",
            request=resp.request, response=resp,
        )
    return resp


# arXiv asks for a contactable User-Agent per their Terms of Use; Semantic Scholar
# is more permissive but we send the same UA for operator visibility.
_USER_AGENT = "PaperHub/0.1 (https://github.com/whats2000/PaperHub)"
# Graph-navigation fields (/references, /citations). These endpoints reject a
# nested ``authors.name`` with HTTP 400 ("Unrecognized or unsupported
# fields: [authors.name]"); a bare ``authors`` returns authorId + name.
_FIELDS = "title,abstract,year,authors,externalIds"
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
    doi: str | None  # extracted from externalIds.DOI when present


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
    doi: str | None  # extracted from externalIds.DOI when present


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
        authors=clean_author_names(
            [a["name"] for a in item.get("authors") or [] if a.get("name")],
        ),
        arxiv_id=(item.get("externalIds") or {}).get("ArXiv"),
    )


def _coerce_hit(item: dict[str, Any]) -> SemanticScholarHit:
    open_pdf = item.get("openAccessPdf") or {}
    external_ids = item.get("externalIds") or {}
    return SemanticScholarHit(
        paperId=str(item.get("paperId") or ""),
        title=item.get("title") or "",
        abstract=item.get("abstract"),
        year=item.get("year"),
        authors=clean_author_names(
            [a["name"] for a in item.get("authors") or [] if a.get("name")],
        ),
        arxiv_id=external_ids.get("ArXiv"),
        open_access_pdf_url=open_pdf.get("url") if isinstance(open_pdf, dict) else None,
        doi=external_ids.get("DOI"),
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
        doi=hit.doi,
    )


async def find_related(
    arxiv_id: str,
    *,
    mode: Mode,
    max_results: int = 8,
    diag: dict[str, Any] | None = None,
) -> list[RelatedPaper]:
    """Return papers related to the given arXiv ID via Semantic Scholar.

    Caller is expected to wrap in tracer.step() — this helper is transport-only.
    When ``diag`` is given it receives the transport facts (``url_path``,
    ``http_status``, ``attempts``, ``retry_after``, ``body_head`` on a non-2xx)
    for the MCP ``_meta`` envelope.
    """
    paper_id = f"arXiv:{arxiv_id}"
    sub_key: str | None
    params = {"limit": str(max_results), "fields": _FIELDS}
    if mode == "cites":
        url = f"{API_BASE}/paper/{paper_id}/references"
        items_key = "data"
        sub_key = "citedPaper"
    elif mode == "cited_by":
        url = f"{API_BASE}/paper/{paper_id}/citations"
        items_key = "data"
        sub_key = "citingPaper"
    else:  # similar
        url = f"{RECOMMENDATIONS_BASE}/papers/forpaper/{paper_id}"
        items_key = "recommendedPapers"
        sub_key = None  # recommendations are paper objects directly
        params["from"] = "all-cs"

    resp = await _get_checked(
        url, params, diag, f"find_related({mode}) for {arxiv_id}",
    )
    raw = resp.json().get(items_key) or []
    items = [(r.get(sub_key) if sub_key else r) for r in raw]
    return [_coerce(i) for i in items if i]


async def search_papers(
    query: str, max_results: int = 8, *, diag: dict[str, Any] | None = None,
) -> list[SemanticScholarHit]:
    """Free-text search across Semantic Scholar's ~200M paper corpus.

    Returns hits with title, abstract, authors, year, plus ``arxiv_id``
    (when ``externalIds.ArXiv`` is present) and ``open_access_pdf_url``
    (when ``openAccessPdf.url`` is present) so the ``ss:`` ingest branch
    can choose the cleanest source. ``diag`` works as in :func:`find_related`.
    """
    url = f"{API_BASE}/paper/search"
    params = {"query": query, "limit": str(max_results), "fields": _SEARCH_FIELDS}
    resp = await _get_checked(url, params, diag, f"search_papers({query!r})")
    data = resp.json().get("data") or []
    return [_coerce_hit(item) for item in data if item]


async def fetch_paper_metadata(paper_id: str) -> SemanticScholarMetadata:
    """Single-paper metadata lookup, used by the ``ss:<paperId>`` ingest
    branch to decide between the arXiv path and the openAccessPdf fallback.
    """
    url = f"{API_BASE}/paper/{paper_id}"
    params = {"fields": _SEARCH_FIELDS}
    resp = await _get_with_retry(url, params, _headers(), _TIMEOUT)
    if resp.status_code == 429:
        raise SemanticScholarRateLimitError(
            f"Semantic Scholar rate-limited fetch_paper_metadata({paper_id!r})",
        )
    resp.raise_for_status()
    return _coerce_metadata(resp.json())
