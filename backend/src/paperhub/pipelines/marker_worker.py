"""Durable, strictly-sequential background Marker upgrade worker (Plan F2.1).

PDF ingest returns instantly on a PyMuPDF baseline and marks the paper
``asset_status='marker_pending'`` when the Marker service is reachable. This
worker drains those pending rows: for each, it re-extracts via Marker, upgrades
the on-disk PaperAsset, re-chunks + re-embeds, and flips ``asset_status`` to
``marker_ready`` (or ``marker_failed`` on error, keeping the PyMuPDF baseline).

It processes ONE paper at a time — concurrent Marker calls OOM a small GPU — and
resumes pending papers across backend restarts (the queue lives in the DB, not
in memory). The heavy lifting is ``PaperPipeline.upgrade_pdf_asset_via_marker``;
this module is a thin sequential scheduler.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import aiosqlite

from paperhub.config import Settings
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.rag.chroma import ChromaStore

logger = logging.getLogger(__name__)


def build_worker_pipeline(
    conn: aiosqlite.Connection, settings: Settings, *, chroma: ChromaStore | None = None,
) -> PaperPipeline:
    """Construct the worker's own ``PaperPipeline``.

    The worker runs on a DEDICATED long-lived aiosqlite connection (the
    lifespan's migration connection is short-lived), so it builds a pipeline
    bound to that connection. Reuses the app's ChromaStore when supplied so
    both share one persistent client; the embedder/marker_client default to the
    process-wide singletons (HTTP-client embedder + Marker service)."""
    return PaperPipeline(
        conn,
        papers_cache_dir=Path(settings.papers_cache_dir),
        chroma=chroma if chroma is not None else ChromaStore(settings.chroma_dir),
    )


async def run_worker(
    pipeline: PaperPipeline,
    conn: aiosqlite.Connection,
    *,
    stop: asyncio.Event,
    max_pages: int | None,
    idle_poll_s: float = 5.0,
) -> None:
    """Drain ``marker_pending`` papers one at a time until ``stop`` is set.

    Each pass selects every pending row (ordered by id) and upgrades them
    sequentially — never concurrently. After draining (or on each failure) the
    worker waits up to ``idle_poll_s`` for ``stop`` before polling again, so it
    exits promptly when asked to stop and otherwise picks up rows enqueued by
    later ingests.
    """
    while not stop.is_set():
        async with conn.execute(
            "SELECT id FROM paper_content "
            "WHERE asset_status = 'marker_pending' ORDER BY id",
        ) as cur:
            pending = [int(r[0]) for r in await cur.fetchall()]

        for pcid in pending:
            if stop.is_set():
                return
            try:
                await pipeline.upgrade_pdf_asset_via_marker(pcid, max_pages=max_pages)
                logger.info("marker upgrade succeeded for paper_content %d", pcid)
            except Exception:
                logger.exception(
                    "marker upgrade failed for paper_content %d; "
                    "keeping PyMuPDF baseline", pcid,
                )
                await conn.execute(
                    "UPDATE paper_content SET asset_status = 'marker_failed' "
                    "WHERE id = ?",
                    (pcid,),
                )
                await conn.commit()

        # Idle until stop is set or the poll interval elapses.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=idle_poll_s)
