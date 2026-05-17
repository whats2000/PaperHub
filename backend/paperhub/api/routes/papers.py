"""POST /papers/import — import a paper from arXiv into PaperHub.

§1.1 Source-fidelity ladder (three-tier):

Tier 1 — LaTeX source (lossless, preferred):
  Use arxiv-latex-mcp (takashiishida/arxiv-latex-mcp).
  Saves artifact to workspace_root/papers/<arxiv_id>/source.tex.
  Sets extraction_tier='latex'.

Tier 2 — Marker (equation-preserving Markdown):
  DEFERRED to Phase B.  Settings.marker_enabled flag (default False)
  allows Phase B to enable without code changes here.

Tier 3 — arxiv-mcp-server raw HTML→Markdown (lossy, last-resort fallback):
  Uses the existing blazickjp/arxiv-mcp-server download_paper route.
  Saves artifact to workspace_root/papers/<arxiv_id>/fallback.md.
  Sets extraction_tier='raw' and notes_md='low_fidelity_extraction'.

Fail-loud rule:
  If both Tier 1 and Tier 3 fail, return HTTP 502 with a message listing
  each tier tried and the error.  Never silently produce an empty paper row.

Column name note:
  ``papers.pdf_path`` now stores the path to the *primary artifact*, which
  may be .tex, .md, or .pdf depending on the tier.  The column name is kept
  as-is (no rename) to avoid breaking the existing model + tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from paperhub.config import Settings, get_settings
from paperhub.data.db import connect
from paperhub.data.models import Paper
from paperhub.data.vectors import ChromaVectorStore, ChunkVector
from paperhub.mcp.client import McpClient
from paperhub.mcp.launchers import McpUpstreamError, make_dispatcher
from paperhub.mcp.scopes import (
    ArxivDownloadPaperArgs,
    ArxivGetAbstractArgs,
    ArxivLatexGetPaperAbstractArgs,
    ArxivLatexGetPaperPromptArgs,
    McpInvocation,
    McpToolScope,
)
from paperhub.rag.chunker import chunk_text
from paperhub.rag.embedder import Embedder
from paperhub.rag.retriever import Retriever

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class PaperImportRequest(BaseModel):
    arxiv_id: str


# ---------------------------------------------------------------------------
# Internal metadata container
# ---------------------------------------------------------------------------


class _PaperMetadata:
    def __init__(
        self,
        title: str,
        authors: list[str],
        year: int | None,
        abstract: str | None,
    ) -> None:
        self.title = title
        self.authors = authors
        self.year = year
        self.abstract = abstract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_arxiv_abstract_response(raw: str, arxiv_id: str) -> _PaperMetadata:
    """Parse arXiv ``get_abstract`` JSON response into known fields (best-effort).

    The upstream returns:
        {status, paper_id, title, authors[], abstract, categories[], published, pdf_url}
    """
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    title: str = str(data.get("title", f"arXiv:{arxiv_id}")).strip()
    authors_raw: Any = data.get("authors", [])
    if isinstance(authors_raw, list):
        authors: list[str] = [str(a) for a in authors_raw]
    elif isinstance(authors_raw, str):
        authors = [a.strip() for a in authors_raw.split(",")]
    else:
        authors = []

    year_raw: Any = data.get("published", data.get("year"))
    year: int | None = None
    if year_raw:
        m = re.search(r"(\d{4})", str(year_raw))
        if m:
            year = int(m.group(1))

    abstract: str | None = data.get("abstract") or data.get("summary")
    if abstract:
        abstract = str(abstract).strip()

    return _PaperMetadata(title=title, authors=authors, year=year, abstract=abstract)


def _parse_arxiv_latex_abstract_response(
    result: dict[str, object], arxiv_id: str
) -> _PaperMetadata:
    """Parse arxiv-latex-mcp ``get_paper_abstract`` response.

    The tool may return plain text (the abstract body) or JSON metadata.
    We attempt JSON parse; if it fails or lacks title/authors, we fall back
    to using the raw text as the abstract with a placeholder title.
    """
    raw = str(result.get("result", ""))
    try:
        data: dict[str, Any] = json.loads(raw)
        title: str = str(data.get("title", f"arXiv:{arxiv_id}")).strip()
        authors_raw: Any = data.get("authors", [])
        if isinstance(authors_raw, list):
            authors: list[str] = [str(a) for a in authors_raw]
        elif isinstance(authors_raw, str):
            authors = [a.strip() for a in authors_raw.split(",")]
        else:
            authors = []
        year_raw: Any = data.get("published", data.get("year"))
        year: int | None = None
        if year_raw:
            m = re.search(r"(\d{4})", str(year_raw))
            if m:
                year = int(m.group(1))
        abstract: str | None = data.get("abstract") or data.get("summary")
        if abstract:
            abstract = str(abstract).strip()
        return _PaperMetadata(title=title, authors=authors, year=year, abstract=abstract)
    except (json.JSONDecodeError, TypeError):
        # Plain-text abstract — use as abstract with placeholder metadata.
        return _PaperMetadata(
            title=f"arXiv:{arxiv_id}",
            authors=[],
            year=None,
            abstract=raw.strip() or None,
        )


def _get_embedder(request: Request, settings: Settings) -> Embedder:
    """Return the app-state cached Embedder, or construct one per-request as fallback.

    Pulls the embedder out of the cached Retriever (if ``get_retriever`` has
    already run for this app instance).  Falls back to per-request construction
    in tests / CI that inject a fake Embedder via monkeypatching.
    """
    retriever: Retriever | None = getattr(request.app.state, "retriever", None)
    if retriever is not None:
        cached_embedder: Embedder | None = getattr(retriever, "_embedder", None)
        if cached_embedder is not None:
            return cached_embedder
    # Fallback: construct per-request (tests that monkeypatch Embedder use this path)
    return Embedder(settings.embedding_model)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/papers/import", response_model=Paper)
async def import_paper(
    request: Request,
    body: PaperImportRequest,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Paper:
    """Import a paper from arXiv into PaperHub.

    Follows the §1.1 three-tier source-fidelity ladder:
    Tier 1 (LaTeX via arxiv-latex-mcp) → Tier 2 (Marker, Phase B) → Tier 3 (raw markdown).

    Parameters
    ----------
    body.arxiv_id:
        The arXiv identifier (e.g. ``"2301.07041"``).
    """
    arxiv_id = body.arxiv_id.strip()
    workspace_root = settings.workspace_root

    # Paper-specific subdirectory (new layout: workspace_root/papers/<arxiv_id>/)
    paper_dir = workspace_root / "papers" / arxiv_id
    paper_dir.mkdir(parents=True, exist_ok=True)

    # Build dispatcher — prefer the pre-launched lifespan-managed dispatcher.
    dispatcher = getattr(request.app.state, "mcp_dispatcher", None)
    if dispatcher is None:
        dispatcher = make_dispatcher(settings=settings)

    # Scopes for both tools
    scopes: dict[str, McpToolScope] = {
        "arxiv": McpToolScope(tool_name="arxiv"),
        "arxiv_latex": McpToolScope(tool_name="arxiv_latex"),
    }
    mcp = McpClient(scopes=scopes, dispatcher=dispatcher)

    tiers_tried: list[tuple[str, str]] = []  # (tier_name, error_summary)
    primary_path: Path | None = None
    extraction_tier: Literal["latex", "marker", "raw"] | None = None
    body_text: str | None = None
    metadata: _PaperMetadata | None = None

    # -----------------------------------------------------------------------
    # Tier 1: arxiv-latex-mcp (LaTeX source — lossless, preferred path)
    # -----------------------------------------------------------------------
    try:
        # Metadata via get_paper_abstract
        abstract_result = await mcp.call(
            McpInvocation(
                tool="arxiv_latex",
                method="get_paper_abstract",
                args=ArxivLatexGetPaperAbstractArgs(arxiv_id=arxiv_id),
            )
        )
        metadata = _parse_arxiv_latex_abstract_response(abstract_result, arxiv_id)

        # If the abstract response didn't include a real title, we'll try to
        # get better metadata from Tier-3 arxiv-mcp-server get_abstract below
        # (after body is fetched) — but only if we can't extract it from LaTeX.

        # LaTeX body via get_paper_prompt
        prompt_result = await mcp.call(
            McpInvocation(
                tool="arxiv_latex",
                method="get_paper_prompt",
                args=ArxivLatexGetPaperPromptArgs(arxiv_id=arxiv_id),
            )
        )
        body_text = str(prompt_result.get("result", "")).strip()

        if not body_text:
            raise McpUpstreamError(
                McpInvocation(
                    tool="arxiv_latex",
                    method="get_paper_prompt",
                    args=ArxivLatexGetPaperPromptArgs(arxiv_id=arxiv_id),
                ),
                "get_paper_prompt returned empty body",
            )

        primary_path = paper_dir / "source.tex"
        primary_path.write_text(body_text, encoding="utf-8")
        extraction_tier = "latex"
        log.info("Imported %s via Tier 1 (LaTeX source)", arxiv_id)

        # If metadata title is still a placeholder, try arxiv-mcp-server for
        # richer structured metadata (title, authors, year) — the LaTeX path
        # is for body content only; metadata can come from the arxiv API.
        if metadata.title == f"arXiv:{arxiv_id}" or not metadata.authors:
            try:
                meta_result = await mcp.call(
                    McpInvocation(
                        tool="arxiv",
                        method="get_abstract",
                        args=ArxivGetAbstractArgs(paper_id=arxiv_id),
                    )
                )
                raw_meta = str(meta_result.get("result", "{}"))
                metadata = _parse_arxiv_abstract_response(raw_meta, arxiv_id)
            except Exception:
                log.info(
                    "Could not enrich metadata from arxiv-mcp-server for %s; "
                    "using arxiv-latex-mcp metadata",
                    arxiv_id,
                )

    except McpUpstreamError as e:
        tiers_tried.append(("latex", str(e)))
        log.info("Tier 1 (LaTeX) failed for %s: %s; falling through to Tier 3", arxiv_id, e)
        primary_path = None
        body_text = None
        metadata = None
        extraction_tier = None

    # -----------------------------------------------------------------------
    # Tier 2: Marker (equation-preserving Markdown) — DEFERRED to Phase B
    # -----------------------------------------------------------------------
    if primary_path is None and settings.marker_enabled:
        # Phase B: implement Marker call here.
        # When enabled, Marker processes the PDF and produces structured Markdown
        # that preserves equations and figure captions.  Set:
        #   primary_path = paper_dir / "marker.md"
        #   extraction_tier = "marker"
        pass

    # -----------------------------------------------------------------------
    # Tier 3: arxiv-mcp-server (raw HTML→Markdown, lossy last resort)
    # -----------------------------------------------------------------------
    if primary_path is None:
        try:
            # Fetch metadata if not already obtained
            if metadata is None:
                meta_result = await mcp.call(
                    McpInvocation(
                        tool="arxiv",
                        method="get_abstract",
                        args=ArxivGetAbstractArgs(paper_id=arxiv_id),
                    )
                )
                raw_meta = str(meta_result.get("result", "{}"))
                metadata = _parse_arxiv_abstract_response(raw_meta, arxiv_id)

            download_result = await mcp.call(
                McpInvocation(
                    tool="arxiv",
                    method="download_paper",
                    args=ArxivDownloadPaperArgs(paper_id=arxiv_id),
                )
            )
            # Upstream returns {status, message, paper_id, source, content}
            body_text = str(download_result.get("content", download_result.get("result", "")))

            if not body_text.strip():
                raise McpUpstreamError(
                    McpInvocation(
                        tool="arxiv",
                        method="download_paper",
                        args=ArxivDownloadPaperArgs(paper_id=arxiv_id),
                    ),
                    "download_paper returned empty content",
                )

            primary_path = paper_dir / "fallback.md"
            primary_path.write_text(body_text, encoding="utf-8")
            extraction_tier = "raw"
            log.warning(
                "Imported %s via Tier 3 (low-fidelity raw markdown fallback)", arxiv_id
            )

        except McpUpstreamError as e:
            tiers_tried.append(("raw", str(e)))
            log.error("Tier 3 (raw markdown) also failed for %s: %s", arxiv_id, e)

    # -----------------------------------------------------------------------
    # All tiers failed
    # -----------------------------------------------------------------------
    if primary_path is None or body_text is None or metadata is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"All import tiers failed for arXiv:{arxiv_id}. "
                f"Tiers tried: {tiers_tried}"
            ),
        )

    # Validate the saved path is inside workspace_root (path-traversal guard)
    try:
        primary_path.resolve().relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Artifact path {primary_path} is outside workspace root {workspace_root}"
        ) from exc

    sha256 = _compute_sha256(primary_path)

    # --- Chunk (directly on text — GROBID / full LaTeX-aware splitting is Phase B) ---
    paper_id = uuid4()
    chunks = list(chunk_text(paper_id, body_text))

    # --- Embed ---
    embedder = _get_embedder(request, settings)
    chunk_texts = [c.text for c in chunks]
    embeddings: list[list[float]] = embedder.embed(chunk_texts) if chunk_texts else []

    # --- Persist paper + chunks to SQLite ---
    notes_md: str | None = "low_fidelity_extraction" if extraction_tier == "raw" else None
    added_at = datetime.now(UTC).isoformat()
    artifact_rel = str(primary_path.relative_to(workspace_root))

    with connect(settings.db_path) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO papers "
                "(id, arxiv_id, doi, title, authors_json, year, abstract,"
                " pdf_path, sha256, added_at, extraction_tier, notes_md) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(paper_id),
                    arxiv_id,
                    metadata.title,
                    json.dumps(metadata.authors),
                    metadata.year,
                    metadata.abstract,
                    artifact_rel,
                    sha256,
                    added_at,
                    extraction_tier,
                    notes_md,
                ),
            )
            for chunk in chunks:
                conn.execute(
                    "INSERT INTO chunks (id, paper_id, section, page, char_start, char_end, text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(chunk.id),
                        str(paper_id),
                        chunk.section,
                        chunk.page,
                        chunk.char_start,
                        chunk.char_end,
                        chunk.text,
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # --- Persist vectors ---
    chroma_path = settings.chroma_path or (workspace_root / "chroma")
    store = ChromaVectorStore(chroma_path)
    vectors: list[ChunkVector] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        chunk_metadata: dict[str, str | int | float | bool] = {"text": chunk.text}
        if chunk.section is not None:
            chunk_metadata["section"] = chunk.section
        if chunk.page is not None:
            chunk_metadata["page"] = chunk.page
        if chunk.char_start is not None:
            chunk_metadata["char_start"] = chunk.char_start
        if chunk.char_end is not None:
            chunk_metadata["char_end"] = chunk.char_end
        vectors.append(
            ChunkVector(
                chunk_id=chunk.id,
                paper_id=paper_id,
                embedding=embedding,
                metadata=chunk_metadata,
            )
        )
    store.add(vectors)

    # --- Return Paper model ---
    return Paper(
        id=paper_id,
        arxiv_id=arxiv_id,
        doi=None,
        title=metadata.title,
        authors=metadata.authors,
        year=metadata.year,
        abstract=metadata.abstract,
        pdf_path=artifact_rel,
        sha256=sha256,
        primary_topic=None,
        added_at=datetime.fromisoformat(added_at),
        extraction_tier=extraction_tier,
        notes_md=notes_md,
    )
