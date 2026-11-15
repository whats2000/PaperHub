"""Trusted search service client (Plan H5a): a SearXNG-compatible JSON API.

The deployer's own search instance replaces the public web-search MCP server
for paper discovery. Results are normalised to the shape ``web.search`` hits
have after ``normalize_mcp_result`` (``[{"title", "url", "snippet"}]``).
A failure never raises into the pipeline: it returns ``[]`` and describes
what happened in ``meta`` (the enrich-then-diagnose fields).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from paperhub.outbound.destinations import (
    OutboundBlocked,
    check_destination,
    httpx_async_request_hook,
)

logger = logging.getLogger(__name__)

TIMEOUT_S = 8.0
_BODY_HEAD = 500
_CALLER = "paper_search:discover"
_KIND = "search"


async def trusted_search(
    query: str,
    *,
    url: str,
    max_results: int = 8,
    meta: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, str]]:
    """``GET {url}/search?q=<query>&format=json``. ``meta`` (if given) is filled
    with ``http_status``, ``count`` and, on failure, ``body_head`` / ``error``."""
    info: dict[str, Any] = meta if meta is not None else {}
    info.update({"http_status": None, "count": 0})
    endpoint = url.rstrip("/") + "/search"
    try:
        check_destination(endpoint, caller=_CALLER, kind=_KIND)
        async with httpx.AsyncClient(
            timeout=TIMEOUT_S,
            follow_redirects=True,
            transport=transport,
            event_hooks={"request": [httpx_async_request_hook(_CALLER, _KIND)]},
        ) as client:
            resp = await client.get(endpoint, params={"q": query, "format": "json"})
    except OutboundBlocked as exc:
        info["error"] = f"blocked: {exc.reason}"
        return []
    except (httpx.InvalidURL, ValueError) as exc:
        info["error"] = f"invalid url: {type(exc).__name__}: {exc}"[:_BODY_HEAD]
        return []
    except httpx.HTTPError as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"[:_BODY_HEAD]
        return []
    info["http_status"] = resp.status_code
    if resp.status_code != 200:
        info["body_head"] = resp.text[:_BODY_HEAD]
        return []
    try:
        results = resp.json()["results"]
        out = [
            {
                "title": str(r.get("title") or ""),
                "url": str(r.get("url") or ""),
                "snippet": str(r.get("content") or ""),
            }
            for r in results[:max_results]
            if isinstance(r, dict)
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        info["body_head"] = resp.text[:_BODY_HEAD]
        info["error"] = "unparseable response"
        return []
    info["count"] = len(out)
    return out


PROBE_TIMEOUT_S = 3.0


async def probe(
    url: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Is the configured trusted search service answering? Never raises.

    Goes through ``check_destination`` like any outbound call. Any HTTP
    answer below 500 counts as reachable (the service is up); a block, a
    timeout or a connection error is not.
    """
    if not url or not url.strip():
        return {"reachable": False, "error": "not_configured"}
    try:
        check_destination(url, caller="settings:probe", kind="search")
        async with httpx.AsyncClient(
            timeout=PROBE_TIMEOUT_S,
            follow_redirects=True,
            transport=transport,
            event_hooks={"request": [httpx_async_request_hook("settings:probe", "search")]},
        ) as client:
            resp = await client.get(url)
    except OutboundBlocked as exc:
        return {"reachable": False, "error": f"blocked: {exc.reason}"}
    except (httpx.InvalidURL, ValueError) as exc:
        return {"reachable": False, "error": f"invalid url: {type(exc).__name__}"}
    except httpx.HTTPError as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {
        "reachable": resp.status_code < 500,
        "http_status": resp.status_code,
    }
