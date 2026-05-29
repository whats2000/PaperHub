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


async def find_oa_pdf_by_doi(doi: str, *, email: str) -> str | None:
    """Return the best open-access PDF URL Unpaywall knows for `doi`, or
    None if no OA version is available / Unpaywall is down / the DOI is
    unknown. Never raises — the dispatcher uses None to mean
    'no URL; fall through to NoIngestibleSourceError'."""
    url = f"{UNPAYWALL_BASE}/{doi}"
    params = {"email": email}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        _LOG.info("unpaywall transport error doi=%s err=%s", doi, exc)
        return None
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
        return None
    try:
        data: dict[str, Any] = resp.json()
    except ValueError:
        return None
    if not data.get("is_oa"):
        return None
    loc = data.get("best_oa_location") or {}
    if not isinstance(loc, dict):
        return None
    return loc.get("url_for_pdf") or loc.get("url") or None
