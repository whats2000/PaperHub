"""Tests for the PaperAsset backfill migration CLI (F2 follow-up).

Backfills the unified PaperAsset (figures+captions, equations→LaTeX, sections)
onto papers already in the cache that predate F2 — Marker for pdf_upload,
LaTeX-source for arxiv/latex_upload. Idempotent (skips rows that already have
asset/figures.json unless --force); strictly sequential at the CLI level so
concurrent Marker calls never OOM the GPU.
"""
import shutil
from pathlib import Path

import httpx

from paperhub.cli.backfill_assets import build_asset_for_paper
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
