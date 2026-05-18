"""Semantic Scholar REST client for citation-graph navigation (SRS v2.3).

Public REST API, free tier (rate-limited to ~100 req / 5 min unauthenticated,
~1 req/s with PAPERHUB_SEMANTIC_SCHOLAR_API_KEY). No auth required for the
demo. See https://api.semanticscholar.org/api-docs/.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx

API_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = httpx.Timeout(10.0)
_FIELDS = "title,abstract,year,authors.name,externalIds"

Mode = Literal["cites", "cited_by", "similar"]


@dataclass(frozen=True)
class RelatedPaper:
    """Semantic Scholar result coerced into PaperHub shape.

    ``arxiv_id`` may be None if the related paper isn't on arXiv —
    ``add_paper_to_session`` will need a non-arXiv ingestion path or
    must skip it. For Plan C we surface but cannot ingest non-arxiv papers.
    """

    title: str
    abstract: str
    year: int | None
    authors: list[str]
    arxiv_id: str | None  # extracted from externalIds.ArXiv when present


def _headers() -> dict[str, str]:
    key = os.environ.get("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY")
    return {"x-api-key": key} if key else {}


def _coerce(item: dict[str, Any]) -> RelatedPaper:
    return RelatedPaper(
        title=item.get("title") or "",
        abstract=item.get("abstract") or "",
        year=item.get("year"),
        authors=[a["name"] for a in item.get("authors") or [] if a.get("name")],
        arxiv_id=(item.get("externalIds") or {}).get("ArXiv"),
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
    resp.raise_for_status()
    raw = resp.json().get(items_key) or []
    items = [(r.get(sub_key) if sub_key else r) for r in raw]
    return [_coerce(i) for i in items if i]
