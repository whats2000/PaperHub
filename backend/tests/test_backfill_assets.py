"""Tests for the PaperAsset backfill migration CLI (F2 follow-up).

Backfills the unified PaperAsset (figures+captions, equations→LaTeX, sections)
onto papers already in the cache that predate F2 — Marker for pdf_upload,
LaTeX-source for arxiv/latex_upload. Idempotent (skips rows that already have
asset/figures.json unless --force); strictly sequential at the CLI level so
concurrent Marker calls never OOM the GPU.
"""
import argparse
import shutil
from pathlib import Path

import aiosqlite
import httpx
import pytest

from paperhub.cli.backfill_assets import build_asset_for_paper, run_backfill
from paperhub.pipelines.marker_client import MarkerClient
from paperhub.pipelines.paper_asset import paper_asset_dir, read_paper_asset

_FIXTURES = Path(__file__).parent / "fixtures"
_MARKER_DOC = _FIXTURES / "marker_doc.json"
_ARXIV_SAMPLE = _FIXTURES / "papers" / "arxiv_sample"


def _marker_client_from_fixture() -> MarkerClient:
    # Return the raw fixture bytes (not re-encoded via json=) — the captured
    # Marker output contains surrogate escapes that httpx's JSON encoder rejects;
    # MarkerClient parses the body with resp.json() the same as the live service.
    raw = _MARKER_DOC.read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    return MarkerClient("http://marker:8002", transport=httpx.MockTransport(handler))


def test_backfill_arxiv_writes_asset(tmp_path: Path) -> None:
    # Copy the arxiv_sample fixture into a tmp cache dir so we don't pollute it
    # with an asset/ dir. Layout mirrors the real cache: <cache>/source/main.tex.
    cache_dir = tmp_path / "arxiv" / "sample"
    source_dir = cache_dir / "source"
    shutil.copytree(_ARXIV_SAMPLE, source_dir)
    main_tex = source_dir / "main.tex"

    res = build_asset_for_paper(
        kind="arxiv",
        source_path=str(main_tex),
        source_dir_path=str(cache_dir),
        title="Sample",
        marker_client=None,  # arxiv path never touches Marker
        max_pages=5,
        force=False,
        dry_run=False,
    )
    assert res.status == "written"
    asset = read_paper_asset(cache_dir)
    assert asset is not None
    assert len(asset.sections) > 0  # \section{} headers extracted


def test_backfill_pdf_uses_marker(tmp_path: Path) -> None:
    cache_dir = tmp_path / "upload" / "abc"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake bytes")  # mock transport ignores content

    res = build_asset_for_paper(
        kind="pdf_upload",
        source_path=str(pdf_path),
        source_dir_path=str(cache_dir),
        title="Attention",
        marker_client=_marker_client_from_fixture(),
        max_pages=None,  # single call; batching is covered in test_marker_client
        force=False,
        dry_run=False,
    )
    assert res.status == "written"
    asset = read_paper_asset(cache_dir)
    assert asset is not None
    # The real fixture is the Transformer paper → its architecture figure (JPEG).
    assert any("Transformer" in f.caption for f in asset.figures)
    fig = asset.figures[0]
    assert (paper_asset_dir(cache_dir) / fig.image_path).exists()


def test_backfill_is_idempotent_and_force_rewrites(tmp_path: Path) -> None:
    cache_dir = tmp_path / "upload" / "abc"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    common = dict(
        kind="pdf_upload", source_path=str(pdf_path),
        source_dir_path=str(cache_dir), title="X", max_pages=None, dry_run=False,
    )
    first = build_asset_for_paper(marker_client=_marker_client_from_fixture(),
                                  force=False, **common)
    assert first.status == "written"
    # Second run without --force: asset/ already exists → skipped (no Marker call).
    second = build_asset_for_paper(marker_client=None, force=False, **common)
    assert second.status == "skipped_exists"
    # With --force it rewrites (Marker called again).
    forced = build_asset_for_paper(marker_client=_marker_client_from_fixture(),
                                   force=True, **common)
    assert forced.status == "written"


def test_backfill_arxiv_via_pdf_routes_to_marker(tmp_path: Path) -> None:
    # An arxiv paper whose LaTeX e-print was unrecoverable falls back to a PDF
    # (source.pdf) at ingest: kind="arxiv" but the source is a PDF → Marker path.
    cache_dir = tmp_path / "arxiv" / "1234.5678"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    res = build_asset_for_paper(
        kind="arxiv",
        source_path=str(pdf_path),
        source_dir_path=str(cache_dir),
        title="X", marker_client=_marker_client_from_fixture(),
        max_pages=None, force=False, dry_run=False,
    )
    assert res.status == "written"
    asset = read_paper_asset(cache_dir)
    assert asset is not None and len(asset.figures) > 0  # Marker yielded figures


def test_backfill_arxiv_empty_dir_skips_gracefully(tmp_path: Path) -> None:
    # arxiv-via-pdf row whose source.pdf was never persisted (empty cache dir).
    cache_dir = tmp_path / "arxiv" / "9999.0000"
    cache_dir.mkdir(parents=True)  # empty: no source.pdf, no .tex
    res = build_asset_for_paper(
        kind="arxiv",
        source_path=str(cache_dir / "source.pdf"),
        source_dir_path=str(cache_dir),
        title="X", marker_client=None, max_pages=None, force=False, dry_run=False,
    )
    assert res.status == "skipped_no_source"


def test_backfill_latex_empty_dir_skips_gracefully(tmp_path: Path) -> None:
    # arxiv LaTeX row whose source/ dir exists but holds no .tex (unrecoverable).
    cache_dir = tmp_path / "arxiv" / "8888.1111"
    source_dir = cache_dir / "source"
    source_dir.mkdir(parents=True)  # empty source/ — no main.tex
    res = build_asset_for_paper(
        kind="arxiv",
        source_path=str(source_dir / "main.tex"),
        source_dir_path=str(cache_dir),
        title="X", marker_client=None, max_pages=5, force=False, dry_run=False,
    )
    assert res.status == "skipped_no_source"


def test_backfill_skips_missing_source(tmp_path: Path) -> None:
    cache_dir = tmp_path / "upload" / "gone"
    res = build_asset_for_paper(
        kind="pdf_upload",
        source_path=str(cache_dir / "missing.pdf"),
        source_dir_path=str(cache_dir),
        title="X", marker_client=None, max_pages=None, force=False, dry_run=False,
    )
    assert res.status == "skipped_no_source"


def test_backfill_dry_run_writes_nothing(tmp_path: Path) -> None:
    cache_dir = tmp_path / "upload" / "abc"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    res = build_asset_for_paper(
        kind="pdf_upload", source_path=str(pdf_path),
        source_dir_path=str(cache_dir), title="X",
        marker_client=_marker_client_from_fixture(), max_pages=None,
        force=False, dry_run=True,
    )
    assert res.status == "dry_run"
    assert read_paper_asset(cache_dir) is None  # nothing written


# ---------------------------------------------------------------------------
# DB-level tests: run_backfill sets asset_status + --enqueue-only
# ---------------------------------------------------------------------------

async def _make_db_with_paper(
    db_path: Path,
    *,
    kind: str,
    source_path: str,
    source_dir_path: str,
    title: str = "Test Paper",
) -> int:
    """Create a minimal paper_content row; return its id."""
    async with aiosqlite.connect(db_path) as conn:
        # Ensure asset_status column exists (mirrors the F2.1 migration).
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_content ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  kind TEXT,"
            "  source_path TEXT,"
            "  source_dir_path TEXT,"
            "  title TEXT,"
            "  asset_status TEXT"
            ")"
        )
        cur = await conn.execute(
            "INSERT INTO paper_content (kind, source_path, source_dir_path, title)"
            " VALUES (?, ?, ?, ?)",
            (kind, source_path, source_dir_path, title),
        )
        await conn.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]


def _args(
    *,
    paper_content_id: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    enqueue_only: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        paper_content_id=paper_content_id,
        dry_run=dry_run,
        force=force,
        enqueue_only=enqueue_only,
    )


@pytest.mark.asyncio
async def test_run_backfill_latex_sets_asset_status_latex(tmp_path: Path) -> None:
    """Normal (blocking) backfill of a LaTeX-source row → asset_status='latex'."""
    cache_dir = tmp_path / "cache"
    source_dir = cache_dir / "source"
    shutil.copytree(_ARXIV_SAMPLE, source_dir)
    main_tex = source_dir / "main.tex"

    db_path = tmp_path / "test.db"
    pcid = await _make_db_with_paper(
        db_path,
        kind="arxiv",
        source_path=str(main_tex),
        source_dir_path=str(cache_dir),
    )

    async with aiosqlite.connect(db_path) as conn:
        await run_backfill(
            _args(paper_content_id=pcid),
            conn=conn,
            marker_client=None,
            max_pages=5,
        )

    async with aiosqlite.connect(db_path) as conn, conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "latex"


@pytest.mark.asyncio
async def test_run_backfill_pdf_sets_asset_status_marker_ready(tmp_path: Path) -> None:
    """Normal (blocking) backfill of a PDF-source row → asset_status='marker_ready'."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    db_path = tmp_path / "test.db"
    pcid = await _make_db_with_paper(
        db_path,
        kind="pdf_upload",
        source_path=str(pdf_path),
        source_dir_path=str(cache_dir),
    )

    async with aiosqlite.connect(db_path) as conn:
        await run_backfill(
            _args(paper_content_id=pcid),
            conn=conn,
            marker_client=_marker_client_from_fixture(),
            max_pages=None,
        )

    async with aiosqlite.connect(db_path) as conn, conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "marker_ready"


@pytest.mark.asyncio
async def test_run_backfill_enqueue_only_pdf_sets_marker_pending(tmp_path: Path) -> None:
    """--enqueue-only on a PDF-source row → asset_status='marker_pending', no Marker call."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    db_path = tmp_path / "test.db"
    pcid = await _make_db_with_paper(
        db_path,
        kind="pdf_upload",
        source_path=str(pdf_path),
        source_dir_path=str(cache_dir),
    )

    # A marker client whose transport raises — any call to it would fail the test.
    def _raise(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Marker should NOT be called in --enqueue-only mode for PDFs")

    no_call_client = MarkerClient("http://marker:8002", transport=httpx.MockTransport(_raise))

    async with aiosqlite.connect(db_path) as conn:
        await run_backfill(
            _args(paper_content_id=pcid, enqueue_only=True),
            conn=conn,
            marker_client=no_call_client,
            max_pages=None,
        )

    # No asset should have been written.
    assert read_paper_asset(cache_dir) is None

    async with aiosqlite.connect(db_path) as conn, conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "marker_pending"


@pytest.mark.asyncio
async def test_run_backfill_enqueue_only_latex_still_builds_sync(tmp_path: Path) -> None:
    """--enqueue-only on a LaTeX-source row → still built synchronously; asset_status='latex'."""
    cache_dir = tmp_path / "cache"
    source_dir = cache_dir / "source"
    shutil.copytree(_ARXIV_SAMPLE, source_dir)
    main_tex = source_dir / "main.tex"

    db_path = tmp_path / "test.db"
    pcid = await _make_db_with_paper(
        db_path,
        kind="arxiv",
        source_path=str(main_tex),
        source_dir_path=str(cache_dir),
    )

    async with aiosqlite.connect(db_path) as conn:
        await run_backfill(
            _args(paper_content_id=pcid, enqueue_only=True),
            conn=conn,
            marker_client=None,
            max_pages=5,
        )

    # Asset should exist on disk.
    asset = read_paper_asset(cache_dir)
    assert asset is not None
    assert len(asset.sections) > 0

    async with aiosqlite.connect(db_path) as conn, conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "latex"


def _snapshot(cache_dir: Path) -> dict[str, bytes]:
    return {
        p.name: p.read_bytes()
        for p in (paper_asset_dir(cache_dir)).glob("*.json")
    }


@pytest.mark.asyncio
async def test_blocks_only_writes_only_blocks_json(tmp_path: Path) -> None:
    """--blocks-only on a paper with an existing asset: adds blocks.json, nothing else moves."""
    from paperhub.pipelines.paper_asset import read_block_assets

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    db_path = tmp_path / "test.db"
    pcid = await _make_db_with_paper(
        db_path, kind="pdf_upload", source_path=str(pdf_path),
        source_dir_path=str(cache_dir),
    )
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, paper_content_id INT, "
            "char_start INT, char_end INT, text TEXT)")
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, char_start, char_end, text) "
            "VALUES (?, 0, 5, 'hello')", (pcid,))
        await conn.commit()
        # Normal backfill first, to have a figures.json / asset_status to protect.
        await run_backfill(_args(paper_content_id=pcid), conn=conn,
                           marker_client=_marker_client_from_fixture(), max_pages=None)
        (paper_asset_dir(cache_dir) / "blocks.json").unlink()  # simulate a pre-H1 paper
        before_files = _snapshot(cache_dir)
        async with conn.execute("SELECT * FROM chunks") as cur:
            before_chunks = await cur.fetchall()
        async with conn.execute("SELECT * FROM paper_content") as cur:
            before_pc = await cur.fetchall()

        args = _args(paper_content_id=pcid)
        args.blocks_only = True
        counts = await run_backfill(args, conn=conn,
                                    marker_client=_marker_client_from_fixture(),
                                    max_pages=None)
        assert counts == {"written": 1}

        async with conn.execute("SELECT * FROM chunks") as cur:
            assert await cur.fetchall() == before_chunks
        async with conn.execute("SELECT * FROM paper_content") as cur:
            assert await cur.fetchall() == before_pc

    after_files = _snapshot(cache_dir)
    assert set(after_files) - set(before_files) == {"blocks.json"}
    for name, data in before_files.items():
        assert after_files[name] == data
    got = read_block_assets(cache_dir)
    assert got is not None and len(got[0]) > 0

    # Idempotent: a second run skips (blocks.json exists) without calling Marker.
    def _raise(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Marker must not be called when blocks.json exists")
    async with aiosqlite.connect(db_path) as conn:
        counts = await run_backfill(
            args, conn=conn,
            marker_client=MarkerClient("http://m", transport=httpx.MockTransport(_raise)),
            max_pages=None)
    assert counts == {"skipped_exists": 1}


def test_blocks_only_skips_latex_sources(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    shutil.copytree(_ARXIV_SAMPLE, cache_dir / "source")
    res = build_asset_for_paper(
        kind="arxiv", source_path=str(cache_dir / "source" / "main.tex"),
        source_dir_path=str(cache_dir), title="S", marker_client=None,
        max_pages=None, force=False, dry_run=False, blocks_only=True,
    )
    assert res.status == "skipped_not_pdf"
    assert not (paper_asset_dir(cache_dir) / "blocks.json").exists()


def test_blocks_only_treats_corrupt_blocks_json_as_missing(tmp_path: Path) -> None:
    from paperhub.pipelines.paper_asset import read_block_assets

    cache_dir = tmp_path / "cache"
    (cache_dir / "asset").mkdir(parents=True)
    (cache_dir / "asset" / "blocks.json").write_text("{trunc", encoding="utf-8")
    pdf = cache_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    res = build_asset_for_paper(
        kind="pdf_upload", source_path=str(pdf), source_dir_path=str(cache_dir),
        title="T", marker_client=_marker_client_from_fixture(), max_pages=None,
        force=False, dry_run=False, blocks_only=True,
    )
    assert res.status == "written"
    assert read_block_assets(cache_dir) is not None


def test_blocks_only_no_source_path_is_skipped_no_source(tmp_path: Path) -> None:
    res = build_asset_for_paper(
        kind="pdf_upload", source_path=None, source_dir_path=str(tmp_path),
        title="T", marker_client=None, max_pages=None,
        force=False, dry_run=False, blocks_only=True,
    )
    assert res.status == "skipped_no_source"
