"""Background fetch of Semantic Scholar citation edges between library docs.

Same loop shape as ``arxiv_metadata_backfill``: DB-queued (survives restarts),
own connection, exponential backoff while Semantic Scholar rate-limits.

* A document is queued when it is non-private, has an ``arxiv_id`` and has no
  ``citation_fetch`` row (or an ``ok``/``not_found`` one older than 30 days; an ``error:*``
  row retries after 1 day). Private documents are never sent to a third party.
* Per document two calls (references, citations). A neighbour whose arXiv id
  matches a library document becomes a ``citation_edges`` row (source cites
  target); everything else is kept in ``citation_external`` for later use.
* A 429 aborts the pass without writing a ``citation_fetch`` row for the
  in-flight document, so it is retried; other per-document failures are
  recorded as ``error:HTTP<status>`` (HTTP errors) or ``error:<ExceptionName>``
  and the pass continues.
* The legacy ``error:HTTPStatusError`` label only came from the earlier
  request bug (S2 rejected ``authors.name`` with a 400 on every document);
  ``apply_schema`` deletes those rows so the documents are fetched again on the
  next pass instead of waiting a day.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable

import aiosqlite
import httpx

from paperhub.db.connection import write_transaction
from paperhub.pipelines.arxiv_metadata_backfill import next_delay
from paperhub.pipelines.semantic_scholar import (
    RelatedPaper,
    SemanticScholarRateLimitError,
    find_related,
)

logger = logging.getLogger(__name__)

BASE_INTERVAL_S = 10 * 60.0
MAX_INTERVAL_S = 6 * 60 * 60.0
INITIAL_DELAY_S = 90.0
# Per-pass document cap and the pause between S2 calls (unauthenticated S2 is
# ~1 request/second shared across the app).
_BATCH = 10
_PAUSE_S = 3.0
_MAX_RESULTS = 100

_VERSION = re.compile(r"v\d+$")

Related = Callable[..., Awaitable[list[RelatedPaper]]]


_SECRET = re.compile(r"(api_key|key|token)=[^&\s]+", re.IGNORECASE)


def _redact(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}=<redacted>", text)


def _body_head(resp: httpx.Response, limit: int = 300) -> str:
    """First ``limit`` chars of an error body, secrets redacted, for the log."""
    try:
        text = resp.text
    except Exception:  # body not read / undecodable
        return "<unavailable>"
    return _redact(text[:limit].replace("\n", " "))


def _norm(arxiv_id: str) -> str:
    return _VERSION.sub("", arxiv_id.strip().lower())


async def fetch_once(
    conn: aiosqlite.Connection,
    *,
    related: Related = find_related,
    pause_s: float = _PAUSE_S,
) -> bool:
    """One pass over up to 10 queued documents. False only on a rate limit."""
    async with conn.execute(
        "SELECT pc.id, pc.arxiv_id FROM paper_content pc "
        "LEFT JOIN citation_fetch cf ON cf.paper_content_id = pc.id "
        "WHERE pc.is_private = 0 AND pc.arxiv_id IS NOT NULL AND ("
        " cf.paper_content_id IS NULL"
        " OR (cf.status IN ('ok','not_found') AND cf.fetched_at < datetime('now','-30 days'))"
        " OR (cf.status NOT IN ('ok','not_found') AND cf.fetched_at < datetime('now','-1 day'))) "
        "ORDER BY pc.id LIMIT ?",
        (_BATCH,),
    ) as cur:
        queue = [(int(r[0]), str(r[1])) for r in await cur.fetchall()]
    if not queue:
        return True

    async with conn.execute(
        "SELECT id, arxiv_id FROM paper_content "
        "WHERE is_private = 0 AND arxiv_id IS NOT NULL",
    ) as cur:
        library = {_norm(str(r[1])): int(r[0]) for r in await cur.fetchall()}

    first = True
    for doc_id, arxiv_id in queue:
        if not first and pause_s:
            await asyncio.sleep(pause_s)
        first = False
        try:
            cites = await related(arxiv_id, mode="cites", max_results=_MAX_RESULTS)
            if pause_s:
                await asyncio.sleep(pause_s)
            cited_by = await related(
                arxiv_id, mode="cited_by", max_results=_MAX_RESULTS,
            )
        except SemanticScholarRateLimitError:
            logger.info(
                "citation edges: Semantic Scholar rate-limited at paper_content "
                "%d (%s); backing off", doc_id, arxiv_id,
            )
            return False
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                kind = type(exc).__name__
                logger.warning(
                    "citation edges: paper_content %d (%s) failed: %s: "
                    "status=%d url=%s body=%s",
                    doc_id, arxiv_id, kind, exc.response.status_code,
                    _redact(str(exc.request.url)), _body_head(exc.response),
                )
                await _record(
                    conn, doc_id, f"error:HTTP{exc.response.status_code}", [], [],
                )
                continue
            logger.info(
                "citation edges: paper_content %d (%s) unknown to Semantic "
                "Scholar (404)", doc_id, arxiv_id,
            )
            await _record(conn, doc_id, "not_found", [], [])
            continue
        except Exception as exc:
            kind = type(exc).__name__
            logger.warning(
                "citation edges: paper_content %d (%s) failed: %s: %s",
                doc_id, arxiv_id, kind, exc,
            )
            await _record(conn, doc_id, f"error:{kind}", [], [])
            continue

        edges: list[tuple[int, int]] = []
        external: list[tuple[int, str, str]] = []
        for papers, direction in ((cites, "cites"), (cited_by, "cited_by")):
            for p in papers:
                if not p.arxiv_id:
                    continue
                other = library.get(_norm(p.arxiv_id))
                if other is None:
                    external.append((doc_id, p.arxiv_id, direction))
                elif other != doc_id:
                    edges.append(
                        (doc_id, other) if direction == "cites" else (other, doc_id),
                    )
        await _record(conn, doc_id, "ok", edges, external)
        logger.info(
            "citation edges: paper_content %d (%s) cites=%d cited_by=%d "
            "internal=%d external=%d",
            doc_id, arxiv_id, len(cites), len(cited_by), len(edges), len(external),
        )
    return True


async def _record(
    conn: aiosqlite.Connection,
    doc_id: int,
    status: str,
    edges: list[tuple[int, int]],
    external: list[tuple[int, str, str]],
) -> None:
    async with write_transaction(conn):
        await conn.executemany(
            "INSERT OR IGNORE INTO citation_edges (source_id, target_id) VALUES (?, ?)",
            edges,
        )
        await conn.executemany(
            "INSERT OR IGNORE INTO citation_external "
            "(paper_content_id, external_arxiv_id, direction) VALUES (?, ?, ?)",
            external,
        )
        await conn.execute(
            "INSERT INTO citation_fetch (paper_content_id, fetched_at, status) "
            "VALUES (?, datetime('now'), ?) "
            "ON CONFLICT(paper_content_id) DO UPDATE SET "
            "fetched_at = excluded.fetched_at, status = excluded.status",
            (doc_id, status),
        )


async def run_citation_loop(
    conn: aiosqlite.Connection,
    *,
    stop: asyncio.Event,
    initial_delay_s: float = INITIAL_DELAY_S,
    base_s: float = BASE_INTERVAL_S,
    max_s: float = MAX_INTERVAL_S,
) -> None:
    """Run fetch passes until ``stop`` is set."""
    wait_s = initial_delay_s
    delay = base_s
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=wait_s)
        if stop.is_set():
            return
        try:
            ok = await fetch_once(conn)
        except Exception:
            logger.exception("citation edges pass failed")
            ok = False
        delay = next_delay(delay, ok=ok, base_s=base_s, max_s=max_s)
        wait_s = delay
