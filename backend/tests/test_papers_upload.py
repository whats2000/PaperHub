"""Tests for POST /papers/upload — multipart PDF ingest endpoint (v2.9-1).

Mirrors the test_papers_api.py pattern: per-test isolated DB via tmp_path +
PAPERHUB_WORKSPACE monkeypatch + create_app(), so no shared state across
tests. The pipeline is exercised end-to-end with a real 1-page sample PDF
fixture (the in-process embedder + reranker are activated by conftest's
PAPERHUB_INPROCESS_MODELS=1 default).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc
from paperhub.pipelines.title_extract import PaperTitleResult


def _build_pdf_with_metadata(
    tmp_path: Path,
    *,
    title: str,
    filename: str = "paper.pdf",
) -> Path:
    """Build a 1-page PDF carrying the given embedded ``title`` metadata."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    doc.new_page()
    doc.set_metadata({  # type: ignore[arg-type]
        "format": "PDF 1.7",
        "title": title,
        "author": "",
        "subject": "",
        "keywords": "",
        "creator": "",
        "producer": "",
        "creationDate": "",
        "modDate": "",
        "trapped": "",
        "encryption": None,
    })
    out = tmp_path / filename
    doc.save(str(out))
    doc.close()
    return out


def _build_pdf_with_page1_title(
    tmp_path: Path,
    *,
    page1_lines: list[tuple[str, float]],
    metadata_title: str = "",
    filename: str = "paper.pdf",
) -> Path:
    """Build a 1-page PDF whose page-1 layout carries title-sized spans.

    Used to exercise the page-1 largest-font fallback path — the typical
    InDesign / Word publisher PDF where ``doc.metadata['title']`` is empty
    but page 1 carries the title at 24-26pt.
    """
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    y = 72.0
    for text, size in page1_lines:
        page.insert_text((72, y), text, fontsize=size)
        y += size + 8
    doc.set_metadata({  # type: ignore[arg-type]
        "format": "PDF 1.7",
        "title": metadata_title,
        "author": "",
        "subject": "",
        "keywords": "",
        "creator": "",
        "producer": "",
        "creationDate": "",
        "modDate": "",
        "trapped": "",
        "encryption": None,
    })
    out = tmp_path / filename
    doc.save(str(out))
    doc.close()
    return out


class _FakeMarker:
    """Stub MarkerClient.extract → a fixed MarkerDoc, so the upload-PDF ingest
    path (which routes through Marker as of F2-T5) runs without a reachable
    Marker service. Returns one text + one figure + one equation block."""

    def extract(self, pdf_bytes: bytes) -> MarkerDoc:
        tiny_png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        return MarkerDoc(
            blocks=[
                MarkerBlock(
                    block_type="Text",
                    html="<p>Body text about a sample paper for ingest tests.</p>",
                    section_hierarchy={"1": "Introduction"},
                    page=1,
                ),
                MarkerBlock(
                    block_type="Figure",
                    html="<p>Figure 1: a figure.</p>",
                    images={"fig.png": tiny_png},
                    section_hierarchy={"1": "Introduction"},
                    page=1,
                ),
                MarkerBlock(
                    block_type="Equation",
                    latex="e=mc^2",
                    section_hierarchy={"1": "Introduction"},
                    page=1,
                ),
            ]
        )


@pytest.fixture(autouse=True)
def _mock_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``get_marker_client`` (lazily resolved by PaperPipeline on first
    PDF ingest) with a fake, so these endpoint tests never hit a real Marker
    service."""
    monkeypatch.setattr(
        "paperhub.pipelines.paper_pipeline.get_marker_client",
        lambda: _FakeMarker(),
    )


async def _seed_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _setup_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    """Point PAPERHUB_WORKSPACE at tmp_path, migrate the DB, seed a session.

    Returns the seeded session_id.
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        return await _seed_session(conn)


@pytest.mark.asyncio
async def test_upload_pdf_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filename-stem fallback path: PDF carries no metadata title AND no
    extractable page-1 title, so the pipeline falls through to the
    upload_path.stem default. Uses a synthesised blank-page PDF — the
    on-disk ``sample.pdf`` fixture would defeat this test because its
    page-1 text contains "A Tiny Test Paper" at large font, which the
    v2.9 page-1-largest-font heuristic would extract."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    pdf_path = _build_pdf_with_metadata(tmp_path, title="")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cache_hit"] is False
    assert body["paper_content_id"] >= 1
    assert body["papers_id"] >= 1
    assert body["title"] == "sample"  # upload_path.stem fallback per pipeline


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/papers/upload",
            data={"session_id": str(session_id)},
            files={"file": ("a.txt", b"hello", "text/plain")},
        )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("PAPERHUB_MAX_UPLOAD_MB", "1")
    app = create_app()
    transport = ASGITransport(app=app)
    big = b"%PDF-1.4\n" + b"\x00" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/papers/upload",
            data={"session_id": str(session_id)},
            files={"file": ("big.pdf", big, "application/pdf")},
        )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_upload_sanitises_path_traversal_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client-supplied ``../../../etc/passwd.pdf`` must be collapsed to
    ``passwd.pdf`` by ``Path(...).name``; the file lands inside the
    tempdir sandbox, and the pipeline's title fallback uses the
    sanitised stem (``passwd``). Pins the existing sandbox behaviour.

    Uses a blank-page PDF so the page-1-largest-font heuristic returns
    empty and the filename-stem fallback is genuinely exercised."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    pdf_path = _build_pdf_with_metadata(tmp_path, title="")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={
                    "file": (
                        "../../../etc/passwd.pdf",
                        f,
                        "application/pdf",
                    ),
                },
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "passwd"


@pytest.mark.asyncio
async def test_upload_filename_sanitises_to_empty_falls_back_to_upload_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the client-supplied filename collapses to an empty ``.name``
    under ``Path(...).name`` (e.g. ``"/"`` → ``""``), the route's
    ``or "upload.pdf"`` second fallback must kick in, yielding a
    pipeline title stem of ``upload``. Pins the defensive fallback
    that prevents an empty-filename UploadFile from writing to the
    tempdir root.

    Uses a blank-page PDF so the page-1-largest-font heuristic returns
    empty and the filename-stem fallback is genuinely exercised."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    pdf_path = _build_pdf_with_metadata(tmp_path, title="")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("/", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "upload"


@pytest.mark.asyncio
async def test_upload_pdf_with_title_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the caller supplies a non-empty ``title`` Form field, the
    pipeline must honour it instead of falling back to ``upload_path.stem``.
    Verifies the override flows all the way through to ``paper_content.title``
    in the DB, not just the response body."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={
                    "session_id": str(session_id),
                    "title": "Custom Title For The Paper",
                },
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Custom Title For The Paper"

    # DB-level assertion — make sure the override actually persisted.
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute(
            "SELECT title FROM paper_content WHERE id = ?",
            (body["paper_content_id"],),
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "Custom Title For The Paper"


@pytest.mark.asyncio
async def test_upload_pdf_blank_title_falls_back_to_filename_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace-only ``title`` must NOT shadow the filename-stem fallback.
    Empty/blank user input is treated as "no override supplied".

    Uses a blank-page PDF so the page-1-largest-font heuristic returns
    empty and the filename-stem fallback is genuinely exercised."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    pdf_path = _build_pdf_with_metadata(tmp_path, title="")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id), "title": "   "},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "sample"


@pytest.mark.asyncio
async def test_upload_pdf_no_title_field_falls_back_to_filename_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: omitting the new ``title`` Form field entirely must
    keep the existing happy-path behaviour (filename-stem fallback).

    Uses a blank-page PDF so the page-1-largest-font heuristic returns
    empty and the filename-stem fallback is genuinely exercised."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    pdf_path = _build_pdf_with_metadata(tmp_path, title="")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "sample"


@pytest.mark.asyncio
async def test_upload_pdf_auto_detects_embedded_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no ``title`` override is supplied and the PDF carries a non-junk
    title in ``doc.metadata['title']`` (publisher-prepared PDFs from Nature,
    Springer, arXiv preprints, etc.), the pipeline must use the embedded
    title instead of the filename stem. Covers the case where a DOI-named
    file like ``s41598-021-94163-y.pdf`` should land with its real title."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    # The filename stem (``s41598-021-94163-y``) is intentionally
    # different from the embedded title — auto-detect must prefer the
    # embedded title.
    pdf_path = _build_pdf_with_metadata(
        tmp_path,
        title="Single-cell RNA sequencing of human breast cancer",
        filename="s41598-021-94163-y.pdf",
    )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={
                    "file": (
                        "s41598-021-94163-y.pdf",
                        f,
                        "application/pdf",
                    ),
                },
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == (
        "Single-cell RNA sequencing of human breast cancer"
    )
    assert body["title"] != "s41598-021-94163-y"

    # DB-level assertion — the auto-detected title must actually persist.
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute(
            "SELECT title FROM paper_content WHERE id = ?",
            (body["paper_content_id"],),
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "Single-cell RNA sequencing of human breast cancer"


@pytest.mark.asyncio
async def test_upload_pdf_auto_detects_page1_title_when_metadata_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``doc.metadata['title']`` is empty (typical InDesign / Word
    publisher PDF — e.g. the user's ``s41598-025-86323-1.pdf`` reproducer)
    but page 1 carries the title at a clearly larger font than authors /
    abstract / journal citation, the pipeline must recover the title via
    the page-1 largest-font heuristic instead of falling back to the
    DOI-named filename stem."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    # NOTE: page1 title is split into two lines so a 26pt single-line render
    # does not overflow the page width and get clipped by PyMuPDF's
    # ``insert_text``. This mirrors the real-world layout where long
    # journal titles wrap onto two lines — the heuristic concatenates them
    # back together via the spans-at-max-size pass.
    pdf_path = _build_pdf_with_page1_title(
        tmp_path,
        metadata_title="",  # the failure mode we're patching
        page1_lines=[
            ("YOLOSeg with applications to", 26),
            ("wafer die particle defect segmentation", 26),
            ("Yen-Ting Li, Yu-Cheng Chan, Po-Hsiang Lin", 10),
            ("Abstract: short body here.", 9),
        ],
        filename="s41598-025-86323-1.pdf",
    )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={
                    "file": (
                        "s41598-025-86323-1.pdf",
                        f,
                        "application/pdf",
                    ),
                },
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == (
        "YOLOSeg with applications to wafer die particle defect segmentation"
    )
    # The DOI-named filename stem must NOT have leaked through.
    assert body["title"] != "s41598-025-86323-1"

    # DB-level assertion — page-1-derived title must actually persist.
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute(
            "SELECT title FROM paper_content WHERE id = ?",
            (body["paper_content_id"],),
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == (
        "YOLOSeg with applications to wafer die particle defect segmentation"
    )


class _UploadStubLlm:
    """Minimal stub used to inject a deterministic LLM response into the
    upload route via ``app.state.llm``. Mirrors the
    ``paperhub.llm.adapter.LlmAdapter`` Protocol's ``structured`` method —
    that's all the title-extract path uses."""

    def __init__(self, title: str | None) -> None:
        self._title = title
        self.call_count = 0

    async def structured(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        response_model: Any,
        model: str,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.call_count += 1
        return PaperTitleResult(title=self._title)

    def stream(self, **kwargs: Any) -> Any:
        raise NotImplementedError("upload path never calls stream")


@pytest.mark.asyncio
async def test_upload_pdf_uses_llm_when_metadata_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When BOTH ``doc.metadata['title']`` is empty AND the page-1 largest-
    font heuristic returns empty (e.g. because the max-font spans concatenate
    to a string longer than the 500-char sanitisation ceiling), the LLM
    fallback must fire and its returned title must win over the filename
    stem. Mirrors the InDesign + Springer reproducer where every metadata
    field is stripped and the font heuristic's first attempt mis-fires."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)

    # Build a page-1 layout where the largest-font spans concatenate to >500
    # chars — this is what makes the existing font heuristic in extract.py
    # return "" (via _sanitise_pdf_title rejecting overlong strings) and so
    # forces the LLM path to engage.
    long_runner_lines: list[tuple[str, float]] = [
        (f"runner line number {i:02d} that contributes to the max-size pool", 24)
        for i in range(12)
    ]
    pdf_path = _build_pdf_with_page1_title(
        tmp_path,
        metadata_title="",
        page1_lines=long_runner_lines,
        filename="s41598-025-86323-1.pdf",
    )

    app = create_app()
    stub = _UploadStubLlm(title="Recovered Title")
    app.state.llm = stub

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pdf_path.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={
                    "file": (
                        "s41598-025-86323-1.pdf",
                        f,
                        "application/pdf",
                    ),
                },
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Recovered Title"
    assert body["title"] != "s41598-025-86323-1"
    assert stub.call_count == 1

    # DB-level assertion — the LLM-derived title must actually persist.
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute(
            "SELECT title FROM paper_content WHERE id = ?",
            (body["paper_content_id"],),
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "Recovered Title"


@pytest.mark.asyncio
async def test_upload_same_bytes_returns_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            first = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
        with sample_pdf.open("rb") as f:
            second = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_body: dict[str, Any] = first.json()
    second_body: dict[str, Any] = second.json()
    assert second_body["cache_hit"] is True
    assert second_body["paper_content_id"] == first_body["paper_content_id"]
