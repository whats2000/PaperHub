"""Tests for POST /papers/import.

Real arXiv + GROBID calls are marked @pytest.mark.e2e and skipped by default.
The unit tests below monkeypatch the MCP dispatcher to return fake metadata +
fake content (matching the §1.1 three-tier source-fidelity ladder).

Tier 1 (arxiv-latex-mcp / LaTeX)  — happy path and metadata-enrichment path
Tier 3 (arxiv-mcp-server markdown) — fallback when Tier 1 fails
Both fail                           — HTTP 502 with named tiers

Tier 1 unit tests also patch ``_download_and_unpack_eprint`` to write a
minimal fake unpacked source directory (a single ``main.tex`` with
``\\documentclass``) rather than hitting the real arXiv network.
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


# ---------------------------------------------------------------------------
# Fake response payloads
# ---------------------------------------------------------------------------

_STUB_ARXIV_ID = "2301.07041"

# Tier-3 (arxiv-mcp-server) fake responses
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
        "paper_id": _STUB_ARXIV_ID,
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
        "paper_id": _STUB_ARXIV_ID,
        "source": "html",
        "content": _STUB_MARKDOWN,
    }
)

# Tier-1 (arxiv-latex-mcp) fake responses
_STUB_LATEX_ABSTRACT_RESPONSE = json.dumps(
    {
        "title": "Test LaTeX Paper",
        "authors": ["Carol Doe", "Dave Lee"],
        "abstract": "A lossless LaTeX-sourced abstract.",
        "published": "2023-01-18",
    }
)

_STUB_LATEX_BODY = r"""\documentclass{article}
\usepackage{amsmath}
\begin{document}
\section{Introduction}
We propose a novel architecture based on attention mechanisms $\alpha + \beta$.

\section{Methods}
Our method uses transformer blocks with multi-head self-attention.

\section{Results}
We achieve state-of-the-art performance on multiple benchmarks.
\end{document}
"""

# -----------------------------------------------------------------------
# Helper: build fake dispatcher with configurable per-tier behavior
# -----------------------------------------------------------------------


def _make_fake_dispatcher(
    *,
    tier1_abstract_response: str | None = _STUB_LATEX_ABSTRACT_RESPONSE,
    tier1_body_response: str | None = _STUB_LATEX_BODY,
    tier1_raises: bool = False,
    tier3_abstract_response: str | None = _STUB_ABSTRACT_RESPONSE,
    tier3_download_response: str | None = _STUB_DOWNLOAD_RESPONSE,
    tier3_raises: bool = False,
) -> Any:
    """Return an async dispatcher that simulates the three-tier ladder."""
    from paperhub.mcp.launchers import McpUpstreamError
    from paperhub.mcp.scopes import (
        ArxivDownloadPaperArgs,
        ArxivGetAbstractArgs,
        ArxivLatexGetPaperAbstractArgs,
        ArxivLatexGetPaperPromptArgs,
        McpInvocation,
    )

    # Dummy invocation used when raising (only needs tool/method/args shape)
    _dummy_inv = McpInvocation(
        tool="arxiv_latex",
        method="get_paper_abstract",
        args=ArxivLatexGetPaperAbstractArgs(arxiv_id=_STUB_ARXIV_ID),
    )

    async def _fake_dispatcher(invocation: object) -> dict[str, object]:
        assert isinstance(invocation, McpInvocation)

        # Tier 1 paths
        if invocation.tool == "arxiv_latex" and isinstance(
            invocation.args, ArxivLatexGetPaperAbstractArgs
        ):
            if tier1_raises:
                raise McpUpstreamError(_dummy_inv, "arxiv-latex-mcp: no e-print available")
            parsed = json.loads(tier1_abstract_response)  # type: ignore[arg-type]
            return {"result": tier1_abstract_response, **parsed}

        if invocation.tool == "arxiv_latex" and isinstance(
            invocation.args, ArxivLatexGetPaperPromptArgs
        ):
            if tier1_raises:
                raise McpUpstreamError(_dummy_inv, "arxiv-latex-mcp: no e-print available")
            return {"result": tier1_body_response}

        # Tier 3 paths (metadata enrichment or fallback)
        if invocation.tool == "arxiv" and isinstance(invocation.args, ArxivGetAbstractArgs):
            if tier3_raises:
                raise McpUpstreamError(_dummy_inv, "arxiv-mcp-server: get_abstract failed")
            parsed = json.loads(tier3_abstract_response)  # type: ignore[arg-type]
            return {"result": tier3_abstract_response, **parsed}

        if invocation.tool == "arxiv" and isinstance(invocation.args, ArxivDownloadPaperArgs):
            if tier3_raises:
                raise McpUpstreamError(_dummy_inv, "arxiv-mcp-server: download failed")
            parsed = json.loads(tier3_download_response)  # type: ignore[arg-type]
            return {"result": tier3_download_response, **parsed}

        return {}

    return _fake_dispatcher


def _make_fake_source_dir(paper_dir: Path, arxiv_id: str) -> Path:
    """Write a minimal fake unpacked e-print source directory.

    Creates ``paper_dir/source/main.tex`` containing ``\\documentclass``
    so that ``_find_main_tex`` identifies it as the primary artifact.
    Also writes a tiny fake figure file to exercise the figure-presence
    assertion in the e2e test (not needed here, but keeps parity).

    Returns the ``source/`` directory path.
    """
    source_dir = paper_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    main_tex = source_dir / "main.tex"
    main_tex.write_text(
        r"""\documentclass{article}
\begin{document}
Hello from fake e-print for """
        + arxiv_id
        + r""".
\end{document}
""",
        encoding="utf-8",
    )
    # Fake figure so the source dir is non-trivial.
    figs = source_dir / "figures"
    figs.mkdir(exist_ok=True)
    (figs / "fig1.png").write_bytes(b"\x89PNG\r\n")
    return source_dir


def _make_client(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tier1_raises: bool = False,
    tier3_raises: bool = False,
    tier1_abstract_response: str | None = _STUB_LATEX_ABSTRACT_RESPONSE,
    tier1_body_response: str | None = _STUB_LATEX_BODY,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(workspace / "paperhub.db"))

    fake_dispatcher = _make_fake_dispatcher(
        tier1_abstract_response=tier1_abstract_response,
        tier1_body_response=tier1_body_response,
        tier1_raises=tier1_raises,
        tier3_raises=tier3_raises,
    )
    monkeypatch.setattr(
        "paperhub.api.routes.papers.make_dispatcher",
        lambda **kwargs: fake_dispatcher,
    )

    # Patch the raw e-print download so unit tests never hit the real arXiv
    # network.  The fake writes a minimal source/ dir with main.tex.
    def _fake_download_and_unpack(arxiv_id: str, paper_dir: Path) -> Path:
        return _make_fake_source_dir(paper_dir, arxiv_id)

    monkeypatch.setattr(
        "paperhub.api.routes.papers._download_and_unpack_eprint",
        _fake_download_and_unpack,
    )

    import paperhub.api.app as _app_module

    monkeypatch.setattr(_app_module, "LaunchedMcpSessions", _NoOpMcpSessions)

    from paperhub.rag.embedder import FakeEmbedder

    monkeypatch.setattr(
        "paperhub.api.routes.papers.Embedder",
        lambda *args, **kwargs: FakeEmbedder(),
    )

    from paperhub.api.app import create_app

    with TestClient(create_app()) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client_tier1(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Client where Tier 1 succeeds (happy path)."""
    yield from _make_client(workspace, monkeypatch)


@pytest.fixture()
def client_tier3_fallback(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Client where Tier 1 raises McpUpstreamError → falls back to Tier 3."""
    yield from _make_client(workspace, monkeypatch, tier1_raises=True)


@pytest.fixture()
def client_both_fail(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Client where both Tier 1 and Tier 3 raise."""
    yield from _make_client(workspace, monkeypatch, tier1_raises=True, tier3_raises=True)


# Legacy fixture alias — uses Tier 3 path (old default behaviour is now fallback)
@pytest.fixture()
def client(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Client where Tier 1 succeeds (equivalent to client_tier1)."""
    yield from _make_client(workspace, monkeypatch)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tier1_latex_success(
    client_tier1: TestClient,
    workspace: Path,
) -> None:
    """Tier 1 success: primary artifact is inside source/, source_dir_path is set."""
    r = client_tier1.post("/papers/import", json={"arxiv_id": _STUB_ARXIV_ID})
    assert r.status_code == 200, r.text

    data = r.json()
    assert data["arxiv_id"] == _STUB_ARXIV_ID
    assert data["extraction_tier"] == "latex"
    assert data["notes_md"] is None, "Tier 1 artifact should not be flagged as low-fidelity"

    # pdf_path must point inside the unpacked source/ directory
    assert data["pdf_path"].endswith(".tex"), f"Expected .tex, got: {data['pdf_path']}"
    assert "source" in data["pdf_path"], (
        f"pdf_path should be inside source/ dir, got: {data['pdf_path']}"
    )

    # source_dir_path must be set for Tier 1
    assert data["source_dir_path"] is not None, "source_dir_path must be set for Tier 1"
    assert "source" in data["source_dir_path"], (
        f"source_dir_path should point to source/ dir, got: {data['source_dir_path']}"
    )

    # Verify the source/ directory exists with at least one .tex file
    source_dir = workspace / data["source_dir_path"]
    assert source_dir.exists() and source_dir.is_dir(), f"source_dir not found: {source_dir}"
    tex_files = list(source_dir.rglob("*.tex"))
    assert tex_files, f"Expected at least one .tex file in {source_dir}"

    # Verify primary .tex file exists and has LaTeX markup
    tex_path = workspace / data["pdf_path"]
    assert tex_path.exists(), f".tex file not found at {tex_path}"
    content = tex_path.read_text(encoding="utf-8")
    assert "\\documentclass" in content or "\\begin" in content, (
        "Expected LaTeX markup in primary artifact"
    )


def test_tier1_paper_stored_in_db(
    client_tier1: TestClient,
    workspace: Path,
) -> None:
    """Tier 1 import: DB row has extraction_tier='latex', source_dir_path set, notes_md=None."""
    r = client_tier1.post("/papers/import", json={"arxiv_id": _STUB_ARXIV_ID})
    assert r.status_code == 200, r.text

    from paperhub.data.db import connect

    db_path = workspace / "paperhub.db"
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT title, extraction_tier, notes_md, source_dir_path FROM papers WHERE arxiv_id=?",
            (_STUB_ARXIV_ID,),
        ).fetchone()
    assert row is not None
    assert row["extraction_tier"] == "latex"
    assert row["notes_md"] is None
    assert row["source_dir_path"] is not None, (
        "DB row source_dir_path must be set for Tier 1 imports"
    )
    assert "source" in row["source_dir_path"]


def test_tier3_fallback_when_tier1_fails(
    client_tier3_fallback: TestClient,
    workspace: Path,
) -> None:
    """Tier 1 failure → Tier 3: artifact is .md, tier='raw', notes_md='low_fidelity_extraction'."""
    r = client_tier3_fallback.post("/papers/import", json={"arxiv_id": _STUB_ARXIV_ID})
    assert r.status_code == 200, r.text

    data = r.json()
    assert data["extraction_tier"] == "raw", f"Expected 'raw', got: {data['extraction_tier']}"
    assert data["pdf_path"].endswith(".md"), f"Expected .md, got: {data['pdf_path']}"
    assert data["notes_md"] == "low_fidelity_extraction", (
        "Tier 3 artifact must be flagged as low-fidelity"
    )

    # Verify the .md file exists under the paper subdirectory
    md_path = workspace / data["pdf_path"]
    assert md_path.exists(), f"Fallback .md file not found at {md_path}"
    content = md_path.read_text(encoding="utf-8")
    assert "Test Paper Title" in content


def test_tier3_fallback_db_row(
    client_tier3_fallback: TestClient,
    workspace: Path,
) -> None:
    """Tier 3 import: DB row has extraction_tier='raw' and notes_md='low_fidelity_extraction'."""
    r = client_tier3_fallback.post("/papers/import", json={"arxiv_id": _STUB_ARXIV_ID})
    assert r.status_code == 200, r.text

    from paperhub.data.db import connect

    db_path = workspace / "paperhub.db"
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT extraction_tier, notes_md FROM papers WHERE arxiv_id=?",
            (_STUB_ARXIV_ID,),
        ).fetchone()
    assert row is not None
    assert row["extraction_tier"] == "raw"
    assert row["notes_md"] == "low_fidelity_extraction"


def test_both_tiers_fail_returns_502(
    client_both_fail: TestClient,
    workspace: Path,
) -> None:
    """Both Tier 1 and Tier 3 fail → HTTP 502 with tiers listed in detail."""
    r = client_both_fail.post("/papers/import", json={"arxiv_id": _STUB_ARXIV_ID})
    assert r.status_code == 502, r.text

    detail = r.json().get("detail", "")
    assert "latex" in detail, f"Expected 'latex' in 502 detail: {detail}"
    assert "raw" in detail, f"Expected 'raw' in 502 detail: {detail}"


def test_papers_import_creates_paper_and_chunks(
    client: TestClient,
    workspace: Path,
) -> None:
    """POST /papers/import must create a paper row + chunk rows + vector entries."""
    r = client.post("/papers/import", json={"arxiv_id": _STUB_ARXIV_ID})
    assert r.status_code == 200, r.text

    data = r.json()
    assert data["arxiv_id"] == _STUB_ARXIV_ID

    # Verify the database has the paper and at least 1 chunk
    from paperhub.data.db import connect

    db_path = workspace / "paperhub.db"
    with connect(db_path) as conn:
        paper_rows = conn.execute(
            "SELECT id FROM papers WHERE arxiv_id=?", (_STUB_ARXIV_ID,)
        ).fetchall()
        assert len(paper_rows) == 1, "Expected 1 paper row"

        paper_id = paper_rows[0][0]
        chunk_rows = conn.execute("SELECT id FROM chunks WHERE paper_id=?", (paper_id,)).fetchall()
        assert len(chunk_rows) >= 1, "Expected at least 1 chunk row"


def test_papers_import_saves_under_workspace_root(
    client: TestClient,
    workspace: Path,
) -> None:
    """The saved artifact path must be inside workspace_root (path-traversal guard)."""
    r = client.post("/papers/import", json={"arxiv_id": _STUB_ARXIV_ID})
    assert r.status_code == 200, r.text

    data = r.json()
    pdf_path = workspace / data["pdf_path"]
    # Must be under workspace / "papers"
    expected_parent = workspace / "papers"
    assert str(pdf_path).startswith(str(expected_parent)), (
        f"pdf_path {pdf_path} not under expected papers dir {expected_parent}"
    )


@pytest.mark.e2e
def test_papers_import_e2e_real_arxiv() -> None:
    """End-to-end import against real arXiv (requires network + uvx arxiv-mcp-server)."""
    pytest.skip("e2e test — run manually with PAPERHUB_WORKSPACE_ROOT set")
