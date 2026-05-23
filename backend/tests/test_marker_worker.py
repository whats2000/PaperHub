"""Tests for the durable, strictly-sequential background Marker upgrade worker
(Plan F2.1).

The worker drains ``asset_status='marker_pending'`` paper_content rows ONE AT A
TIME (concurrent Marker calls OOM the GPU): it re-extracts each PDF via Marker,
upgrades the on-disk PaperAsset to Marker quality, re-chunks + re-embeds from
Marker's cleaner structure, and flips ``asset_status`` to ``marker_ready`` (or
``marker_failed`` on error, keeping the PyMuPDF baseline).
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import aiosqlite
import httpx
import numpy as np
import pytest
import pytest_asyncio

from paperhub.pipelines.marker_client import MarkerClient
from paperhub.pipelines.paper_asset import read_paper_asset, write_paper_asset
from paperhub.pipelines.paper_pipeline import PaperPipeline
from paperhub.pipelines.pymupdf_to_asset import pymupdf_to_asset
from paperhub.rag.chroma import ChromaStore

_FIXTURES = Path(__file__).parent / "fixtures"
_MARKER_DOC = _FIXTURES / "marker_doc.json"
_SAMPLE_PDF = _FIXTURES / "papers" / "sample.pdf"


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> np.ndarray:
        rng = np.random.RandomState(7)
        vecs = rng.randn(len(texts), 384).astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.where(norms > 0, norms, 1.0)


def _marker_client_from_fixture() -> MarkerClient:
    # Return the raw fixture bytes (not re-encoded via json=) — the captured
    # Marker output contains surrogate escapes that httpx's JSON encoder rejects.
    raw = _MARKER_DOC.read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=raw, headers={"content-type": "application/json"}
        )

    return MarkerClient("http://marker:8002", transport=httpx.MockTransport(handler))


def _marker_client_raising() -> MarkerClient:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("marker unreachable")

    return MarkerClient("http://marker:8002", transport=httpx.MockTransport(handler))


def _make_pipeline(
    conn: aiosqlite.Connection, tmp_path: Path, marker_client: MarkerClient,
) -> tuple[PaperPipeline, ChromaStore]:
    chroma = ChromaStore(tmp_path / "chroma")
    pipeline = PaperPipeline(
        conn,
        papers_cache_dir=tmp_path / "papers_cache",
        chroma=chroma,
        embedder=_FakeEmbedder(),
        marker_client=marker_client,
    )
    return pipeline, chroma


async def _seed_pending_pdf(
    conn: aiosqlite.Connection,
    chroma: ChromaStore,
    cache_dir: Path,
    *,
    content_key: str,
) -> int:
    """Insert a kind='pdf_upload' paper_content row with asset_status=
    'marker_pending', a real PDF on disk, a PyMuPDF baseline asset, and some
    initial chunks + Chroma vectors — exactly the state PDF ingest leaves."""
    cache_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — test seed; sync fs is fine
    pdf_path = cache_dir / "source.pdf"
    pdf_path.write_bytes(_SAMPLE_PDF.read_bytes())

    # Baseline PyMuPDF asset (degraded — figures with no captions).
    asset = pymupdf_to_asset(pdf_path, source_dir=cache_dir)
    write_paper_asset(asset, cache_dir)

    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, sha256, title, authors_json, year, abstract, "
        "sections_json, source_path, source_dir_path, html_path, asset_status) "
        "VALUES (?, 'pdf_upload', ?, 'A Tiny Test Paper', '[]', 2024, '', "
        "?, ?, ?, ?, 'marker_pending')",
        (
            content_key,
            content_key.split(":", 1)[1],
            "[]",
            str(pdf_path),
            str(cache_dir),
            str(cache_dir / "source.html"),
        ),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])

    # Seed some initial chunks + Chroma vectors (the PyMuPDF-baseline chunks).
    initial_texts = ["initial baseline chunk one", "initial baseline chunk two"]
    chunk_ids: list[int] = []
    for i, t in enumerate(initial_texts):
        await conn.execute(
            "INSERT INTO chunks "
            "(paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, 'Full text', ?, ?, ?)",
            (pcid, i * 10, i * 10 + 10, t),
        )
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            r = await cur.fetchone()
        assert r is not None
        chunk_ids.append(int(r[0]))
    await conn.commit()
    embedder = _FakeEmbedder()
    chroma.add_chunks(
        paper_content_id=pcid,
        chunk_ids=chunk_ids,
        texts=initial_texts,
        embeddings=embedder.embed(initial_texts),
    )
    return pcid


@pytest_asyncio.fixture
async def conn(tmp_path: Path):
    from paperhub.db.migrate import apply_schema

    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as c:
        await c.execute("PRAGMA foreign_keys = ON")
        await apply_schema(c)
        yield c


async def _chunk_texts(conn: aiosqlite.Connection, pcid: int) -> list[str]:
    async with conn.execute(
        "SELECT text FROM chunks WHERE paper_content_id = ? ORDER BY id", (pcid,)
    ) as cur:
        return [str(r[0]) for r in await cur.fetchall()]


async def _asset_status(conn: aiosqlite.Connection, pcid: int) -> str:
    async with conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return str(row[0])


@pytest.mark.asyncio
async def test_upgrade_pdf_asset_via_marker_upgrades_and_reembeds(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    pipeline, chroma = _make_pipeline(conn, tmp_path, _marker_client_from_fixture())
    cache_dir = tmp_path / "papers_cache" / "upload" / "abc"
    pcid = await _seed_pending_pdf(conn, chroma, cache_dir, content_key="sha256:abc")

    before_texts = await _chunk_texts(conn, pcid)

    await pipeline.upgrade_pdf_asset_via_marker(pcid, max_pages=None)

    # asset_status flipped to ready.
    assert await _asset_status(conn, pcid) == "marker_ready"

    # The on-disk asset is now Marker quality (real caption present).
    asset = read_paper_asset(cache_dir)
    assert asset is not None
    assert any("Transformer" in f.caption for f in asset.figures)
    # Marker figure files use sniffed extensions (the fixture is a JPEG).
    assert any(f.image_path.endswith(".jpg") for f in asset.figures)

    # Chunks were replaced from Marker's cleaner structure (text changed).
    after_texts = await _chunk_texts(conn, pcid)
    assert after_texts != before_texts
    assert "initial baseline chunk one" not in after_texts


@pytest.mark.asyncio
async def test_worker_failure_keeps_baseline_and_marks_failed(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    from paperhub.pipelines.marker_worker import run_worker

    pipeline, chroma = _make_pipeline(conn, tmp_path, _marker_client_raising())
    cache_dir = tmp_path / "papers_cache" / "upload" / "fail"
    pcid = await _seed_pending_pdf(conn, chroma, cache_dir, content_key="sha256:fail")
    before_texts = await _chunk_texts(conn, pcid)

    stop = asyncio.Event()

    async def _stop_after_drain() -> None:
        # Let the worker make one full pass, then stop it.
        await asyncio.sleep(0.2)
        stop.set()

    await asyncio.gather(
        run_worker(pipeline, conn, stop=stop, max_pages=None, idle_poll_s=0.05),
        _stop_after_drain(),
    )

    assert await _asset_status(conn, pcid) == "marker_failed"
    # Prior chunks + asset untouched (baseline preserved).
    assert await _chunk_texts(conn, pcid) == before_texts
    assert read_paper_asset(cache_dir) is not None


@pytest.mark.asyncio
async def test_worker_is_sequential_and_stops(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    from paperhub.pipelines.marker_worker import run_worker

    # A transport that records the max number of concurrently in-flight calls.
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    raw = _MARKER_DOC.read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        try:
            return httpx.Response(
                200, content=raw, headers={"content-type": "application/json"}
            )
        finally:
            with lock:
                in_flight -= 1

    marker = MarkerClient("http://marker:8002", transport=httpx.MockTransport(handler))
    pipeline, chroma = _make_pipeline(conn, tmp_path, marker)

    pcid1 = await _seed_pending_pdf(
        conn, chroma, tmp_path / "papers_cache" / "upload" / "one",
        content_key="sha256:one",
    )
    pcid2 = await _seed_pending_pdf(
        conn, chroma, tmp_path / "papers_cache" / "upload" / "two",
        content_key="sha256:two",
    )

    stop = asyncio.Event()

    async def _watch() -> None:
        # Poll until both rows are ready, then stop the worker.
        for _ in range(200):
            s1 = await _asset_status(conn, pcid1)
            s2 = await _asset_status(conn, pcid2)
            if s1 == "marker_ready" and s2 == "marker_ready":
                break
            await asyncio.sleep(0.02)
        stop.set()

    await asyncio.gather(
        run_worker(pipeline, conn, stop=stop, max_pages=None, idle_poll_s=0.02),
        _watch(),
    )

    assert max_in_flight <= 1
    assert await _asset_status(conn, pcid1) == "marker_ready"
    assert await _asset_status(conn, pcid2) == "marker_ready"
    assert stop.is_set()
