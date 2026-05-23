"""Bounded live check of the F2.1 background worker (T8).

Enqueues one PDF paper (asset_status='marker_pending'), runs the REAL
`run_worker` loop against the LIVE marker service + in-process embedder, and
confirms it drains to 'marker_ready' with a re-chunked + upgraded asset.

Run (from backend/):
    $env:PAPERHUB_WORKSPACE=(Resolve-Path workspace).Path
    $env:PAPERHUB_INPROCESS_MODELS=1
    uv run python scripts/f2_1_worker_check.py <paper_content_id>
"""
from __future__ import annotations

import asyncio
import sys

from paperhub.config import load_settings
from paperhub.db.connection import configure_connection
from paperhub.pipelines.marker_worker import build_worker_pipeline, run_worker
from paperhub.rag.chroma import ChromaStore

import aiosqlite


async def _count_chunks(conn: aiosqlite.Connection, pcid: int) -> int:
    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _status(conn: aiosqlite.Connection, pcid: int) -> str | None:
    async with conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


async def main() -> None:
    pcid = int(sys.argv[1])
    settings = load_settings()
    chroma = ChromaStore(settings.chroma_dir)

    async with aiosqlite.connect(settings.db_path) as conn:
        await configure_connection(conn)
        # Enqueue: mark this paper pending so the worker picks it up.
        await conn.execute(
            "UPDATE paper_content SET asset_status='marker_pending' WHERE id=?", (pcid,)
        )
        await conn.commit()
        before = await _count_chunks(conn, pcid)
        print(f"pcid={pcid}: enqueued marker_pending; chunks_before={before}")

        pipeline = build_worker_pipeline(conn, settings, chroma=chroma)
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_worker(pipeline, conn, stop=stop, max_pages=settings.marker_max_pages,
                       idle_poll_s=2.0)
        )

        # Poll until the worker resolves this paper (ready or failed), then stop.
        for _ in range(600):  # up to ~20 min
            await asyncio.sleep(2)
            st = await _status(conn, pcid)
            if st in ("marker_ready", "marker_failed"):
                break
        stop.set()
        await asyncio.wait_for(task, timeout=30)

        after = await _count_chunks(conn, pcid)
        final = await _status(conn, pcid)
        print(f"pcid={pcid}: final asset_status={final}; chunks_after={after}")


if __name__ == "__main__":
    asyncio.run(main())
