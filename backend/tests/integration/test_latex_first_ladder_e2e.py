"""Live e2e tests for the §1.1 three-tier source-fidelity ladder.

Skipped by default; run via:

    cd backend; uv run pytest -m e2e -v

Required environment:
- ANTHROPIC_API_KEY or GEMINI_API_KEY in the environment (or .env)
- network access to arxiv.org
- ``uvx`` on PATH (arxiv-latex-mcp + arxiv-mcp-server are auto-fetched by uvx
  on first call)

Tests in this module:
1. test_latex_first_import_real_arxiv
   Imports "Attention Is All You Need" (1706.03762) via Tier 1 (arxiv-latex-mcp).
   Asserts: extraction_tier='latex', pdf_path ends with .tex, .tex file has LaTeX body.

2. test_chat_paper_qa_against_latex_import
   Full pipeline: /papers/import (Tier 1) → /chat paper_qa → Gemini (or Anthropic)
   generates an answer that mentions "Transformer".
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_latex_first_import_real_arxiv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real arXiv import via Tier 1 (LaTeX) succeeds; artifact is .tex.

    Uses arxiv ID 1706.03762 (Attention Is All You Need) — known to have
    LaTeX e-print available.
    """
    # Load .env so GEMINI_API_KEY / ANTHROPIC_API_KEY are available even when
    # not set in the shell environment (common when running via `uv run pytest`).
    from dotenv import load_dotenv

    load_dotenv(override=False)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        pytest.skip("No LLM API key configured — skipping e2e")

    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))

    from paperhub.api.app import create_app
    from paperhub.config import get_settings
    from paperhub.data.db import apply_migrations
    from paperhub.mcp.launchers import LaunchedMcpSessions

    # ASGITransport does not trigger the ASGI lifespan.  We apply migrations
    # manually and pre-launch MCP sessions in the same asyncio task to avoid
    # the D3 anyio cancel-scope mismatch when sessions are created lazily.
    e2e_settings = get_settings()
    apply_migrations(e2e_settings.db_path)

    app = create_app()

    async with LaunchedMcpSessions(e2e_settings) as mcp_sessions:
        app.state.mcp_dispatcher = mcp_sessions.make_dispatcher()

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            r = await ac.post(
                "/papers/import",
                json={"arxiv_id": "1706.03762"},
                timeout=180.0,
            )
            assert r.status_code == 200, f"import failed: {r.status_code} {r.text}"
            paper = r.json()
            assert paper["arxiv_id"] == "1706.03762"
            assert paper["title"], "Expected non-empty title"
            assert paper["extraction_tier"] == "latex", (
                f"Expected extraction_tier='latex', got: {paper['extraction_tier']}"
            )
            assert paper["pdf_path"].endswith(".tex"), (
                f"Expected .tex artifact, got: {paper['pdf_path']}"
            )
            assert paper["notes_md"] is None, (
                "Tier 1 artifact should not be flagged as low-fidelity"
            )

            # Verify the .tex file actually exists and looks like LaTeX
            tex_path = tmp_path / paper["pdf_path"]
            assert tex_path.exists(), f".tex file not found at {tex_path}"
            content = tex_path.read_text(encoding="utf-8")
            assert "\\" in content, "Expected LaTeX backslash commands in .tex file"
            assert len(content) > 1000, f"Expected substantial LaTeX body, got {len(content)} chars"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_latex_first_import_preserves_raw_source_and_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import a real arXiv paper; verify the unpacked e-print is on disk
    AND figures (.png/.pdf/.jpg/.eps) are present in source/.

    Uses 1706.03762 (Attention Is All You Need) — has figures in the source.
    Per SRS v1.10 §1.1 Tier 1: the primary artifact is the UNPACKED e-print
    archive (figures + bib + sty + .tex), not just flattened LaTeX text.
    """
    from dotenv import load_dotenv

    load_dotenv(override=False)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        pytest.skip("No LLM API key configured — skipping e2e")

    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))

    from paperhub.api.app import create_app
    from paperhub.config import get_settings
    from paperhub.data.db import apply_migrations
    from paperhub.mcp.launchers import LaunchedMcpSessions

    e2e_settings = get_settings()
    apply_migrations(e2e_settings.db_path)

    app = create_app()

    async with LaunchedMcpSessions(e2e_settings) as mcp_sessions:
        app.state.mcp_dispatcher = mcp_sessions.make_dispatcher()

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            r = await ac.post(
                "/papers/import",
                json={"arxiv_id": "1706.03762"},
                timeout=300.0,
            )
            assert r.status_code == 200, f"import failed: {r.status_code} {r.text}"
            paper = r.json()
            assert paper["extraction_tier"] == "latex"
            assert paper["source_dir_path"], "source_dir_path must be set for Tier 1 imports"

            source_dir = tmp_path / paper["source_dir_path"]
            assert source_dir.exists() and source_dir.is_dir(), (
                f"source/ directory not found at {source_dir}"
            )

            # At least one .tex file must be present
            tex_files = list(source_dir.rglob("*.tex"))
            assert tex_files, f"Expected at least one .tex file in {source_dir}"

            # At least one figure (.png / .pdf / .jpg / .jpeg / .eps)
            figure_exts = {".png", ".pdf", ".jpg", ".jpeg", ".eps"}
            figures = [p for p in source_dir.rglob("*") if p.suffix.lower() in figure_exts]
            assert figures, (
                f"Expected at least one figure (.png/.pdf/.jpg/.jpeg/.eps) in "
                f"{source_dir}; got files: "
                f"{[p.relative_to(source_dir) for p in source_dir.rglob('*') if p.is_file()][:20]}"
            )

            # pdf_path (primary .tex) must be inside source/
            pdf_path = tmp_path / paper["pdf_path"]
            assert pdf_path.exists(), f"Primary .tex not found at {pdf_path}"
            assert str(pdf_path).startswith(str(source_dir)), (
                f"pdf_path {pdf_path} is not inside source_dir {source_dir}"
            )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_chat_paper_qa_against_latex_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full /papers/import (Tier 1) → /chat paper_qa → answer with citation.

    Live test: real arxiv-latex-mcp + real Gemini (or Anthropic). Verifies
    the LaTeX-first path produces a chunked, retrievable, answerable paper.
    """
    from dotenv import load_dotenv

    load_dotenv(override=False)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        pytest.skip("No LLM API key configured — skipping e2e")

    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))

    from paperhub.api.app import create_app
    from paperhub.config import get_settings
    from paperhub.data.db import apply_migrations
    from paperhub.mcp.launchers import LaunchedMcpSessions

    # ASGITransport does not trigger the ASGI lifespan automatically.
    # We apply migrations and pre-launch MCP sessions to avoid the D3
    # anyio cancel-scope mismatch when sessions are created lazily per-request.
    e2e_settings = get_settings()
    apply_migrations(e2e_settings.db_path)

    app = create_app()

    # Pre-launch MCP sessions in the same asyncio task (lifespan emulation).
    # This sets app.state.mcp_dispatcher so the route avoids the lazy-connect path.
    async with LaunchedMcpSessions(e2e_settings) as mcp_sessions:
        dispatcher = mcp_sessions.make_dispatcher()
        app.state.mcp_dispatcher = dispatcher

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            import_r = await ac.post(
                "/papers/import", json={"arxiv_id": "1706.03762"}, timeout=180.0
            )
            assert import_r.status_code == 200, (
                f"import failed: {import_r.status_code} {import_r.text}"
            )
            assert import_r.json()["extraction_tier"] == "latex", "Expected Tier 1 (latex) import"

            # Chat: ask about the architecture
            events: list[dict[str, object]] = []
            async with ac.stream(
                "POST",
                "/chat",
                json={
                    "message": "What architecture does this paper propose?",
                    "session_id": None,
                },
                timeout=120.0,
            ) as response:
                assert response.status_code == 200, f"chat failed: {response.status_code}"
                buf = ""
                async for chunk in response.aiter_text():
                    buf += chunk
                    # SSE frames are separated by \r\n\r\n or \n\n
                    # Normalize to \n\n for uniform parsing
                    normalized = buf.replace("\r\n", "\n")
                    while "\n\n" in normalized:
                        frame, remainder = normalized.split("\n\n", 1)
                        buf = remainder
                        normalized = remainder
                        for line in frame.splitlines():
                            if line.startswith("data: "):
                                events.append(json.loads(line[len("data: ") :]))

        event_types = [e.get("type") for e in events]
        assert "routing_decision" in event_types, (
            f"missing routing_decision in SSE events: {event_types}"
        )
        assert "final" in event_types, f"missing final in SSE events: {event_types}"

        final = next(e for e in reversed(events) if e.get("type") == "final")
        answer = final.get("answer", "")
        assert isinstance(answer, str) and answer, (
            f"Empty or missing answer in final event: {final}"
        )
        # Loose check: the answer should mention "Transformer" (the actual answer)
        assert "Transformer" in answer or "transformer" in answer.lower(), (
            f"Expected 'Transformer' in answer, got: {answer[:300]}"
        )
