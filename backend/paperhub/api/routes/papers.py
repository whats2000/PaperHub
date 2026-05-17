"""POST /papers/import — import a paper from arXiv into PaperHub.

§1.1 Source-fidelity ladder (three-tier):

Tier 1 — LaTeX source (lossless, preferred):
  Downloads the raw e-print tarball via the ``arxiv`` Python library and
  unpacks it into workspace_root/papers/<arxiv_id>/source/ (figures + bib
  + sty + .tex files).  Also calls arxiv-latex-mcp get_paper_prompt to
  obtain the flattened LaTeX text, saved as source/source.flattened.tex
  for RAG chunking.  Sets extraction_tier='latex'.

  pdf_path      → primary .tex file inside source/ (contains \\documentclass)
  source_dir_path → papers/<arxiv_id>/source/ (relative to workspace_root)

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

import gzip
import hashlib
import json
import logging
import re
import tarfile
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


def _download_and_unpack_eprint(arxiv_id: str, paper_dir: Path) -> Path:
    """Download the raw e-print tarball and unpack it into ``paper_dir/source/``.

    Returns the path to the unpacked ``source/`` directory.

    Strategy
    --------
    1. Use ``arxiv.Client`` to download the e-print to
       ``paper_dir/<arxiv_id>.tar.gz``.
    2. Try to open as a tarball (most e-prints are ``.tar.gz`` multi-file
       archives).  If ``tarfile.ReadError`` is raised, the e-print is a
       single gzip-compressed ``.tex`` file — decompress it directly.
    3. Tarball extraction uses Python 3.12's ``filter='data'`` where
       available for extra safety.  Additionally, every member path is
       checked to ensure it resolves inside ``source/`` (path-traversal
       guard).

    Raises
    ------
    RuntimeError
        If the download fails or the tarball cannot be opened.
    """
    import arxiv as _arxiv  # type: ignore[import-untyped]

    source_dir = paper_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    tarball_filename = f"{arxiv_id}.tar.gz"
    tarball_path = paper_dir / tarball_filename

    # Download — arxiv.Client().results() is a generator; take the first hit.
    client = _arxiv.Client()
    search = _arxiv.Search(id_list=[arxiv_id])
    result = next(client.results(search), None)
    if result is None:
        raise RuntimeError(f"arxiv library returned no results for {arxiv_id!r}")

    # download_source() returns the path to the saved file.
    saved_path: Path = Path(
        result.download_source(
            dirpath=str(paper_dir),
            filename=tarball_filename,
        )
    )
    log.info("Downloaded e-print for %s → %s", arxiv_id, saved_path)
    # download_source may save with a different name if the content type differs.
    if saved_path != tarball_path:
        tarball_path = saved_path

    # Unpack — try tarball first, fall back to single-file gzip.
    source_dir_resolved = source_dir.resolve()

    try:
        with tarfile.open(tarball_path) as tar:
            members = tar.getmembers()
            # Path-traversal guard: refuse any member whose resolved path
            # escapes source_dir.
            for member in members:
                member_path = (source_dir / member.name).resolve()
                if not str(member_path).startswith(str(source_dir_resolved)):
                    raise ValueError(f"Tarball escape attempt blocked: {member.name!r}")
            # Python ≥3.12 is required (pyproject.toml); data filter is always available.
            tar.extractall(source_dir, filter="data")
    except tarfile.ReadError:
        # Single-file e-print: gzip-compressed .tex source.
        tex_out = source_dir / f"{arxiv_id}.tex"
        with gzip.open(tarball_path, "rb") as gz_in, tex_out.open("wb") as out:
            out.write(gz_in.read())
        log.info("e-print for %s was single-file gzip .tex → %s", arxiv_id, tex_out)

    return source_dir


def _find_main_tex(source_dir: Path) -> Path | None:
    """Return the most-likely main ``.tex`` file in *source_dir*.

    Heuristic (in priority order):
    1. The only ``.tex`` file containing ``\\documentclass`` — unambiguous winner.
    2. Prefer a file named ``main.tex``, ``paper.tex``, or matching the parent
       directory name (arxiv_id).
    3. Among candidates, pick the largest by byte size.
    4. Return ``None`` if no ``.tex`` file exists at all.
    """
    candidates = [p for p in source_dir.rglob("*.tex") if p.is_file()]
    if not candidates:
        return None

    with_docclass = [
        p
        for p in candidates
        if "\\documentclass" in p.read_text(encoding="utf-8", errors="replace")
    ]
    if not with_docclass:
        # Unusual — fall back to all .tex files
        with_docclass = candidates

    if len(with_docclass) == 1:
        return with_docclass[0]

    # Multiple candidates — prefer well-known names.
    preferred_stems = {"main", "paper", source_dir.parent.name}
    preferred = [p for p in with_docclass if p.stem.lower() in preferred_stems]
    pool = preferred if preferred else with_docclass
    # Largest file wins among the pool.
    return max(pool, key=lambda p: p.stat().st_size)


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
    source_dir_path: Path | None = None
    extraction_tier: Literal["latex", "marker", "raw"] | None = None
    body_text: str | None = None
    metadata: _PaperMetadata | None = None

    # -----------------------------------------------------------------------
    # Tier 1: raw e-print download + arxiv-latex-mcp flattened text (lossless)
    #
    # Primary artifact: unpacked e-print archive in paper_dir/source/
    #   (figures + bib + sty + .tex — needed by Phase B slide pipeline so
    #   \includegraphics paths resolve at compile time).
    # Secondary artifact: flattened LaTeX text saved as
    #   paper_dir/source/source.flattened.tex (used by the RAG chunker).
    # -----------------------------------------------------------------------
    try:
        # Step 1 — Metadata via arxiv-latex-mcp get_paper_abstract
        abstract_result = await mcp.call(
            McpInvocation(
                tool="arxiv_latex",
                method="get_paper_abstract",
                args=ArxivLatexGetPaperAbstractArgs(arxiv_id=arxiv_id),
            )
        )
        metadata = _parse_arxiv_latex_abstract_response(abstract_result, arxiv_id)

        # Step 2 — Flattened LaTeX body via arxiv-latex-mcp get_paper_prompt
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

        # Step 3 — Download + unpack raw e-print into paper_dir/source/
        #   This is the PRIMARY Tier-1 artifact (SRS §1.1): the full unpacked
        #   archive (figures + .bib + .sty + .tex) is required by the Phase B
        #   slide pipeline so \includegraphics paths resolve at compile time.
        unpacked_dir = _download_and_unpack_eprint(arxiv_id, paper_dir)
        source_dir_path = unpacked_dir

        # Save flattened text as secondary RAG artifact inside source/
        flattened_path = unpacked_dir / "source.flattened.tex"
        flattened_path.write_text(body_text, encoding="utf-8")

        # Identify primary .tex (the one with \documentclass)
        main_tex = _find_main_tex(unpacked_dir)
        if main_tex is None:
            # Fallback: use the flattened text file as the primary artifact
            primary_path = flattened_path
        else:
            primary_path = main_tex

        extraction_tier = "latex"
        log.info(
            "Imported %s via Tier 1 (unpacked e-print: %s, main .tex: %s)",
            arxiv_id,
            unpacked_dir,
            primary_path,
        )

        # Step 4 — Enrich metadata from arxiv-mcp-server if placeholder title
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
        source_dir_path = None
        body_text = None
        metadata = None
        extraction_tier = None
    except Exception as e:
        # Raw download or unpack failed (network, rate-limit, withdrawn paper, etc.)
        tiers_tried.append(("latex", f"e-print download/unpack failed: {e}"))
        log.info(
            "Tier 1 e-print download failed for %s: %s; falling through to Tier 3",
            arxiv_id,
            e,
        )
        primary_path = None
        source_dir_path = None
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
            log.warning("Imported %s via Tier 3 (low-fidelity raw markdown fallback)", arxiv_id)

        except McpUpstreamError as e:
            tiers_tried.append(("raw", str(e)))
            log.error("Tier 3 (raw markdown) also failed for %s: %s", arxiv_id, e)

    # -----------------------------------------------------------------------
    # All tiers failed
    # -----------------------------------------------------------------------
    if primary_path is None or body_text is None or metadata is None:
        raise HTTPException(
            status_code=502,
            detail=(f"All import tiers failed for arXiv:{arxiv_id}. Tiers tried: {tiers_tried}"),
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

    # --- Compute relative paths for persistence ---
    notes_md: str | None = "low_fidelity_extraction" if extraction_tier == "raw" else None
    added_at = datetime.now(UTC).isoformat()
    artifact_rel = str(primary_path.relative_to(workspace_root))
    source_dir_rel: str | None = (
        str(source_dir_path.relative_to(workspace_root)) if source_dir_path is not None else None
    )

    with connect(settings.db_path) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO papers "
                "(id, arxiv_id, doi, title, authors_json, year, abstract,"
                " pdf_path, sha256, added_at, extraction_tier, notes_md, source_dir_path) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    source_dir_rel,
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
        source_dir_path=source_dir_rel,
    )
