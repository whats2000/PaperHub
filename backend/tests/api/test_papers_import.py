"""Tests for POST /papers/import.

Real arXiv + GROBID calls are marked @pytest.mark.e2e and skipped by default.
The unit test below monkeypatches the MCP dispatcher to return fake metadata +
fake markdown content (matching the real ``get_abstract`` + ``download_paper``
tool surface from blazickjp/arxiv-mcp-server).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# No-op LaunchedMcpSessions for test fixtures
# ---------------------------------------------------------------------------


class _NoOpMcpSessions:
    """Stand-in for LaunchedMcpSessions that never starts a subprocess.

    Prevents the TestClient lifespan from launching ``uvx arxiv-mcp-server``
    (which takes ~90s and sets app.state.mcp_dispatcher, bypassing fake
    dispatchers patched by monkeypatch).
    """

    def __init__(self, settings: Any) -> None:
        pass

    async def __aenter__(self) -> _NoOpMcpSessions:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        pass

    def make_dispatcher(self) -> None:
        return None


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# Fake markdown content returned by the real ``download_paper`` tool
_STUB_MARKDOWN = (
    """# Test Paper Title

## Abstract

A test abstract about machine learning.

## Introduction

This is the first paragraph of the test paper.

This is the second paragraph with more content about deep learning.

## Methods

We propose a novel approach using transformers.
"""
    * 5
)  # repeat to ensure enough text for chunking

_STUB_ABSTRACT_RESPONSE = json.dumps(
    {
        "status": "success",
        "paper_id": "2301.07041",
        "title": "Test Paper Title",
        "authors": ["Alice Smith", "Bob Jones"],
        "abstract": "A test abstract about machine learning.",
        "categories": ["cs.LG", "cs.AI"],
        "published": "2023-01-18",
        "pdf_url": "https://arxiv.org/pdf/2301.07041",
    }
)

_STUB_DOWNLOAD_RESPONSE = json.dumps(
    {
        "status": "success",
        "message": "Paper downloaded successfully",
        "paper_id": "2301.07041",
        "source": "html",
        "content": _STUB_MARKDOWN,
    }
)


@pytest.fixture()
def client(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    # Patch make_dispatcher in the papers route to return a fake dispatcher
    async def _fake_dispatcher(invocation: object) -> dict[str, object]:
        from paperhub.mcp.scopes import (
            ArxivDownloadPaperArgs,
            ArxivGetAbstractArgs,
            McpInvocation,
        )

        assert isinstance(invocation, McpInvocation)
        if invocation.tool == "arxiv" and isinstance(invocation.args, ArxivGetAbstractArgs):
            # Return the parsed dict (launchers.py merges parsed JSON into result)
            parsed = json.loads(_STUB_ABSTRACT_RESPONSE)
            return {"result": _STUB_ABSTRACT_RESPONSE, **parsed}
        if invocation.tool == "arxiv" and isinstance(invocation.args, ArxivDownloadPaperArgs):
            parsed = json.loads(_STUB_DOWNLOAD_RESPONSE)
            return {"result": _STUB_DOWNLOAD_RESPONSE, **parsed}
        return {}

    monkeypatch.setattr(
        "paperhub.api.routes.papers.make_dispatcher",
        lambda **kwargs: _fake_dispatcher,
    )

    # Prevent the lifespan from starting the real arXiv MCP subprocess.
    # Without this the TestClient lifespan would launch ``uvx arxiv-mcp-server``
    # (taking up to 90s) and set app.state.mcp_dispatcher to a real dispatcher
    # that bypasses the fake above.
    import paperhub.api.app as _app_module

    monkeypatch.setattr(_app_module, "LaunchedMcpSessions", _NoOpMcpSessions)

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

    # Verify the saved file is a .md file under workspace_root
    pdf_path = data["pdf_path"]
    assert pdf_path.endswith(".md"), f"Expected .md extension, got: {pdf_path}"
    saved_path = Path(pdf_path)
    assert saved_path.is_relative_to(workspace) or str(saved_path).startswith(str(workspace)), (
        f"Saved path {saved_path} is not under workspace {workspace}"
    )

    # Verify the markdown file was actually written
    assert saved_path.exists(), f"Markdown file not found at {saved_path}"
    content = saved_path.read_text(encoding="utf-8")
    assert "Test Paper Title" in content

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


def test_papers_import_saves_under_workspace_root(
    client: TestClient,
    workspace: Path,
) -> None:
    """The saved markdown file path must be inside workspace_root (path-traversal guard)."""
    r = client.post("/papers/import", json={"arxiv_id": "2301.07041"})
    assert r.status_code == 200, r.text

    data = r.json()
    pdf_path = Path(data["pdf_path"])
    # Must be under workspace / "papers"
    expected_parent = workspace / "papers"
    assert str(pdf_path).startswith(str(expected_parent)), (
        f"pdf_path {pdf_path} not under expected papers dir {expected_parent}"
    )
    assert pdf_path.suffix == ".md"


@pytest.mark.e2e
def test_papers_import_e2e_real_arxiv() -> None:
    """End-to-end import against real arXiv (requires network + uvx arxiv-mcp-server)."""
    pytest.skip("e2e test — run manually with PAPERHUB_WORKSPACE_ROOT set")
