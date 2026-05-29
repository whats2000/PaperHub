"""Unpaywall OA-PDF lookup (F4.3).

Secondary "find me a free PDF for this DOI" service used by the
``ss:`` dispatch when SS itself didn't carry an ``openAccessPdf.url``.
Free, no API key — requires only an email for abuse-control logging.
Soft-fails on every error path so an Unpaywall outage never breaks
the dispatcher's outer try/raise contract.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
_TIMEOUT_S = 8.0

_LOG = logging.getLogger(__name__)


async def find_oa_pdf_urls_by_doi(doi: str, *, email: str) -> list[str]:
    """Return ordered list of OA PDF/landing URLs Unpaywall knows for `doi`,
    or empty list if no OA / Unpaywall is down / DOI unknown.

    Order: best_oa_location first, then any other oa_locations in Unpaywall
    order, deduped. Per location, prefer url_for_pdf, fall back to url.
    Never raises (Unpaywall outages return [], not exception)."""
    url = f"{UNPAYWALL_BASE}/{doi}"
    params = {"email": email}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        _LOG.info("unpaywall transport error doi=%s err=%s", doi, exc)
        return []
    if resp.status_code != 200:
        log_level = (
            logging.WARNING
            if resp.status_code == 429 or resp.status_code >= 500
            else logging.INFO
        )
        _LOG.log(
            log_level,
            "unpaywall non-200 doi=%s status=%d", doi, resp.status_code,
        )
        return []
    try:
        data: dict[str, Any] = resp.json()
    except ValueError:
        return []
    if not data.get("is_oa"):
        return []

    # Collect URLs in priority order: best_oa_location first, then oa_locations
    candidates: list[dict[str, Any]] = []
    best = data.get("best_oa_location")
    if isinstance(best, dict):
        candidates.append(best)
    for loc in data.get("oa_locations") or []:
        if isinstance(loc, dict):
            candidates.append(loc)

    seen: set[str] = set()
    urls: list[str] = []
    for loc in candidates:
        u = loc.get("url_for_pdf") or loc.get("url")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls
