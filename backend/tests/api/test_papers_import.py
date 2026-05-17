"""Tests for POST /papers/import.

Real arXiv + GROBID calls are marked @pytest.mark.e2e and skipped by default.
The unit test below monkeypatches the MCP dispatcher to return fake metadata +
a local PDF path, and monkeypatches GROBID to return stub TEI.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def fake_pdf(workspace: Path) -> Path:
    """Write a minimal PDF-like file inside the workspace."""
    pdf_path = workspace / "papers" / "2301.07041.pdf"
    pdf_path.parent.mkdir(parents=True)
    # Write some text that the chunker can process
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf content for testing purposes " * 50)
    return pdf_path


_STUB_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a" type="main">Test Paper Title</title>
      </titleStmt>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <p>This is the first paragraph of the test paper.</p>
      <p>This is the second paragraph with more content about deep learning.</p>
    </body>
  </text>
</TEI>"""

_STUB_METADATA = json.dumps(
    {
        "title": "Test Paper Title",
        "authors": ["Alice Smith", "Bob Jones"],
        "published": "2023-01-18",
        "abstract": "A test abstract about machine learning.",
    }
)


@pytest.fixture()
def client(
    workspace: Path,
    fake_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    # Patch make_dispatcher in the papers route to return a fake dispatcher
    async def _fake_dispatcher(invocation: object) -> dict[str, object]:
        from paperhub.mcp.scopes import (
            ArxivDownloadPdfArgs,
            ArxivFetchMetadataArgs,
            GrobidProcessFulltextArgs,
            McpInvocation,
        )

        assert isinstance(invocation, McpInvocation)
        if invocation.tool == "arxiv" and isinstance(invocation.args, ArxivFetchMetadataArgs):
            return {"result": _STUB_METADATA}
        if invocation.tool == "arxiv" and isinstance(invocation.args, ArxivDownloadPdfArgs):
            return {"result": str(fake_pdf)}
        if invocation.tool == "grobid" and isinstance(invocation.args, GrobidProcessFulltextArgs):
            return {"tei": _STUB_TEI}
        return {}

    monkeypatch.setattr(
        "paperhub.api.routes.papers.make_dispatcher",
        lambda **kwargs: _fake_dispatcher,
    )

    # Patch Embedder to avoid loading sentence-transformers
    from paperhub.rag.embedder import FakeEmbedder

    monkeypatch.setattr(
        "paperhub.api.routes.papers.Embedder",
        lambda *args, **kwargs: FakeEmbedder(),
    )

    from paperhub.api.app import create_app

    with TestClient(create_app()) as tc:
        yield tc


def test_papers_import_creates_paper_and_chunks(
    client: TestClient,
    workspace: Path,
) -> None:
    """POST /papers/import must create a paper row + chunk rows + vector entries."""
    r = client.post("/papers/import", json={"arxiv_id": "2301.07041"})
    assert r.status_code == 200, r.text

    data = r.json()
    assert data["arxiv_id"] == "2301.07041"
    assert data["title"] == "Test Paper Title"
    assert "Alice Smith" in data["authors"]
    assert data["year"] == 2023

    # Verify the database has the paper and at least 1 chunk
    from paperhub.data.db import connect

    db_path = workspace / "paperhub.db"
    with connect(db_path) as conn:
        paper_rows = conn.execute(
            "SELECT id FROM papers WHERE arxiv_id=?", ("2301.07041",)
        ).fetchall()
        assert len(paper_rows) == 1, "Expected 1 paper row"

        paper_id = paper_rows[0][0]
        chunk_rows = conn.execute("SELECT id FROM chunks WHERE paper_id=?", (paper_id,)).fetchall()
        assert len(chunk_rows) >= 1, "Expected at least 1 chunk row"


@pytest.mark.e2e
def test_papers_import_e2e_real_arxiv() -> None:
    """End-to-end import against real arXiv + GROBID (requires network + GROBID)."""
    pytest.skip("e2e test — run manually with PAPERHUB_WORKSPACE_ROOT set and GROBID running")
