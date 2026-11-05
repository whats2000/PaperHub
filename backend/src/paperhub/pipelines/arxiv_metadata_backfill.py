"""Background arXiv metadata backfill.

When an arXiv paper is ingested while the arXiv API is rate-limited, the
pipeline stores it with a source-derived title (LaTeX ``\\title{}`` / PDF
metadata) and empty authors/year. This loop fills those rows in later.

Rate-limit friendly by design — arXiv's 429 window can last a long time:

* ONE batched ``id_list`` request per pass for every pending paper
  (``fetch_arxiv_by_ids``, no per-call retries).
* No request at all when nothing is pending (only a DB query).
* Exponential backoff while the API stays unavailable (base → ×2 → … → cap),
  reset to the base interval after any successful pass.

The queue is the DB itself (``kind='arxiv'`` rows with empty authors or a NULL
year), so pending papers survive restarts. A successful fetch writes arXiv's
canonical title + authors + year, and the abstract only when it is empty (an
existing Semantic Scholar abstract is kept).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import aiosqlite

from paperhub.pipelines.arxiv_client import fetch_arxiv_by_ids

logger = logging.getLogger(__name__)

BASE_INTERVAL_S = 15 * 60.0
MAX_INTERVAL_S = 6 * 60 * 60.0
# Let boot (MCP launch, marker worker, first UI polls) settle before the
# first pass.
INITIAL_DELAY_S = 60.0
# id_list batch cap per request — keeps the query URL a sane length.
_BATCH = 50


async def backfill_once(conn: aiosqlite.Connection) -> bool:
    """One backfill pass. Returns False only when the arXiv API was
    unavailable (caller backs off); True otherwise, including when nothing
    was pending (no request made)."""
    async with conn.execute(
        "SELECT id, arxiv_id FROM paper_content "
        "WHERE kind = 'arxiv' AND arxiv_id IS NOT NULL "
        "AND (authors_json = '[]' OR year IS NULL) "
        "ORDER BY id LIMIT ?",
        (_BATCH,),
    ) as cur:
        rows = [(int(r[0]), str(r[1])) for r in await cur.fetchall()]
    if not rows:
        return True

    found = await asyncio.to_thread(fetch_arxiv_by_ids, [aid for _, aid in rows])
    if found is None:
        logger.info(
            "arXiv metadata backfill: API unavailable; %d paper(s) still pending",
            len(rows),
        )
        return False

    filled = 0
    for pcid, aid in rows:
        r = found.get(aid)
        if r is None:
            continue
        await conn.execute(
            "UPDATE paper_content SET title = ?, authors_json = ?, year = ?, "
            "abstract = CASE WHEN abstract = '' THEN ? ELSE abstract END "
            "WHERE id = ?",
            (r.title, json.dumps(r.authors), r.year, r.abstract, pcid),
        )
        filled += 1
        logger.info(
            "arXiv metadata backfill: paper_content %d (%s) -> %r",
            pcid, aid, r.title,
        )
    await conn.commit()
    missing = len(rows) - filled
    if missing:
        logger.warning(
            "arXiv metadata backfill: %d of %d id(s) unknown to arXiv",
            missing, len(rows),
        )
    return True


def next_delay(current: float, *, ok: bool, base_s: float, max_s: float) -> float:
    """Base interval after success; double (capped) while unavailable."""
    return base_s if ok else min(current * 2, max_s)


async def run_backfill(
    conn: aiosqlite.Connection,
    *,
    stop: asyncio.Event,
    initial_delay_s: float = INITIAL_DELAY_S,
    base_s: float = BASE_INTERVAL_S,
    max_s: float = MAX_INTERVAL_S,
) -> None:
    """Run backfill passes until ``stop`` is set."""
    wait_s = initial_delay_s
    delay = base_s
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=wait_s)
        if stop.is_set():
            return
        try:
            ok = await backfill_once(conn)
        except Exception:
            logger.exception("arXiv metadata backfill pass failed")
            ok = False
        delay = next_delay(delay, ok=ok, base_s=base_s, max_s=max_s)
        wait_s = delay
