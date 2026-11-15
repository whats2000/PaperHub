"""Destination allowlist: the cheapest outbound checkpoint stage (Plan H5a).

Every HTTP client calls :func:`check_destination` before a request. The check
is synchronous so the arXiv client (plain code, worker threads) can use it
too; a block writes one ``blocked`` row through :func:`log.append_sync`.

Matching is exact on the host (case-insensitive, port ignored): ``arxiv.org``
does not imply ``export.arxiv.org`` — both are listed by default. Allowed
requests are not logged here; the text checkpoint logs text-bearing ones.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from paperhub.outbound import log
from paperhub.outbound.policy import OutboundPolicy, load_policy, locked_keys

logger = logging.getLogger(__name__)

#: Pseudo-destination for the public web-search MCP server (no single host).
PUBLIC_WEB_SEARCH = "public-web-search"

_PAPER_SOURCES = frozenset({"export.arxiv.org", "arxiv.org", "api.semanticscholar.org",
                            "api.unpaywall.org"})  # Unpaywall: OA lookup by DOI
_REASON = "destination not allowed"


class OutboundBlocked(Exception):
    """The destination is not allowed by the outbound policy."""

    def __init__(
        self, destination: str, reason: str = _REASON, log_id: int | None = None,
    ) -> None:
        super().__init__(f"outbound destination blocked: {destination} ({reason})")
        self.destination = destination
        self.reason = reason
        #: The ``outbound_log`` row of this refusal, when it is known (a row
        #: written off the event loop has no id yet; ``log_blocked=False``
        #: leaves the row to the caller).
        self.log_id = log_id


def host_of(url: str) -> str:
    """Lower-cased host of ``url`` (port, credentials ignored); ``""`` if none."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _trusted_host(policy: OutboundPolicy) -> str:
    """The trusted-search host the allowlist admits on its own, or ``""``.

    A trusted URL set from the Settings panel must not widen an allowlist the
    deployer locked: the host is admitted only when the URL is locked too, or
    when the allowlist is not locked (then the UI could widen it anyway)."""
    if not policy.trusted_search_url:
        return ""
    locked = locked_keys()
    if ("PAPERHUB_OUTBOUND_ALLOWLIST" in locked
            and "PAPERHUB_TRUSTED_SEARCH_URL" not in locked):
        return ""
    return host_of(policy.trusted_search_url)


def check_destination(
    url: str,
    *,
    caller: str,
    kind: str,
    conn: sqlite3.Connection | None = None,
    log_blocked: bool = True,
) -> None:
    """Raise :class:`OutboundBlocked` (and log a ``blocked`` row) unless ``url``
    may be contacted. Only the host is logged: the path and query may carry
    user text. ``log_blocked=False`` skips the row: the caller writes its own
    (the outbound checkpoint, which needs the row id)."""
    policy = load_policy()
    if url == PUBLIC_WEB_SEARCH:
        if policy.public_web_search:
            return
        dest, reason = PUBLIC_WEB_SEARCH, "public web search is off"
    else:
        host = host_of(url)
        if policy.allowlist is None:
            return
        trusted = _trusted_host(policy)
        if host and (host in policy.allowlist or host == trusted):
            return
        dest, reason = host or "(invalid url)", _REASON
    log_id = _log_block(conn, caller, kind, dest, reason) if log_blocked else None
    raise OutboundBlocked(dest, reason, log_id)


# Strong references to in-flight off-loop log writes (a bare future can be
# garbage-collected mid-flight). Tests await ``pending_log_writes()``.
_pending: set[asyncio.Future[int | None]] = set()


def pending_log_writes() -> list[asyncio.Future[int | None]]:
    return list(_pending)


def _write_block(
    conn: sqlite3.Connection | None, caller: str, kind: str, dest: str, reason: str
) -> int | None:
    try:
        return log.append_sync(
            conn, actor="system", caller=caller, kind=kind, destination=dest,
            decision="blocked", stage="allowlist", reason=reason,
        )
    except Exception:  # noqa: BLE001 - a logging failure must never lift the block
        logger.exception("outbound: could not log a blocked destination %s", dest)
        return None


def _log_block(
    conn: sqlite3.Connection | None, caller: str, kind: str, dest: str, reason: str
) -> int | None:
    """Write the ``blocked`` row and return its id. Inside a running event
    loop (and with no caller-owned connection) the write goes to the executor
    so the loop never stalls (id unknown: None); the block itself stays
    synchronous and fail-closed."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None or conn is not None:
        return _write_block(conn, caller, kind, dest, reason)
    fut = loop.run_in_executor(None, _write_block, None, caller, kind, dest, reason)
    _pending.add(fut)
    fut.add_done_callback(_pending.discard)
    return None


def httpx_async_request_hook(
    caller: str, kind: str
) -> Callable[[httpx.Request], Awaitable[None]]:
    """An ``AsyncClient`` request hook: the allowlist is checked on the first
    request and on every redirect hop, before the hop is contacted."""
    async def hook(request: httpx.Request) -> None:
        check_destination(str(request.url), caller=caller, kind=kind)

    return hook


_S2_HOST = "api.semanticscholar.org"


def search_destinations() -> list[str]:
    """Where a paper search may send its queries under the current policy:
    the web search (trusted service, else public web search when on) and
    Semantic Scholar, each only when the allowlist admits it. The "Allow
    this search?" card shows them (H5a)."""
    policy = load_policy()
    trusted = _trusted_host(policy)
    out: list[str] = []
    if policy.trusted_search_url:
        host = host_of(policy.trusted_search_url)
        if host and (host == trusted or policy.allowlist is None
                     or host in policy.allowlist):
            out.append(host)
    elif policy.public_web_search:
        out.append(PUBLIC_WEB_SEARCH)
    if policy.allowlist is None or _S2_HOST in policy.allowlist:
        out.append(_S2_HOST)
    return out


def list_destinations() -> list[dict[str, str]]:
    """The hosts PaperHub may contact, with who put them there."""
    policy = load_policy()
    trusted = _trusted_host(policy)
    out: list[dict[str, str]] = []
    if policy.allowlist is None:
        out.append({"host": "*", "source": "deployer"})
    else:
        for h in sorted(policy.allowlist):
            if h == trusted:
                continue
            out.append({"host": h, "source": "paper_source" if h in _PAPER_SOURCES else "deployer"})
    if trusted:
        out.append({"host": trusted, "source": "trusted_search"})
    if policy.public_web_search:
        out.append({"host": PUBLIC_WEB_SEARCH, "source": "public_search"})
    return out
