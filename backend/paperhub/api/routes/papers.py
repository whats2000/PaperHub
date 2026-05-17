"""POST /papers/import — import a paper from arXiv into PaperHub.

Phase A pipeline (aligned to real arxiv-mcp-server tool surface):
1. Call arxiv MCP ``get_abstract`` → extract title, authors, year, abstract.
2. Call arxiv MCP ``download_paper`` → get markdown content (NOT a PDF path).
3. Save markdown to ``workspace_root/papers/<arxiv_id>.md``.
4. Validate the saved path is inside workspace_root.
5. Chunk via ``chunker.chunk_text`` (operates on markdown text directly —
   GROBID is skipped in Phase A; the markdown is already clean text).
6. Embed via ``Embedder`` (instantiated once in app lifespan; D6 fix).
7. Insert ``papers`` + ``chunks`` rows into SQLite.
8. Insert vectors into ChromaVectorStore.
9. Return the created ``Paper`` Pydantic model.

Phase A notes:
- ``papers.pdf_path`` stores the ``.md`` file path; column name is kept as-is
  for now and will be renamed or documented in Phase B (migration deferred).
- SHA-256 is computed from the markdown file bytes.
- GROBID code remains in the codebase for Phase B (FR-02 PDF references).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from paperhub.config import Settings, get_settings
from paperhub.data.db import connect
from paperhub.data.models import Paper
from paperhub.data.vectors import ChromaVectorStore, ChunkVector
from paperhub.mcp.client import McpClient
from paperhub.mcp.launchers import make_dispatcher
from paperhub.mcp.scopes import (
    ArxivDownloadPaperArgs,
    ArxivGetAbstractArgs,
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
# Helpers
# ---------------------------------------------------------------------------


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_arxiv_abstract_response(raw: str, arxiv_id: str) -> dict[str, Any]:
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

    return {"title": title, "authors": authors, "year": year, "abstract": abstract}


def _get_embedder(request: Request, settings: Settings) -> Embedder:
    """Return the app-state cached Embedder, or construct one per-request as fallback.

    Pulls the embedder out of the cached Retriever (if ``get_retriever`` has
    already run for this app instance).  Falls back to per-request construction
    in tests / CI that inject a fake Embedder via monkeypatching.
    """
    retriever: Retriever | None = getattr(request.app.state, "retriever", None)
    if retriever is not None:
        # Pull the embedder out of the cached Retriever
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

    Parameters
    ----------
    body.arxiv_id:
        The arXiv identifier (e.g. ``"2301.07041"``).
    """
    arxiv_id = body.arxiv_id.strip()
    workspace_root = settings.workspace_root

    # Build dispatcher — prefer the pre-launched lifespan-managed dispatcher
    # (stored on app.state by the lifespan hook in api/app.py) which avoids
    # spawning a new subprocess per-request and fixes the D3 anyio cancel-scope
    # mismatch.  Falls back to make_dispatcher() for tests / lazy connect.
    dispatcher = getattr(request.app.state, "mcp_dispatcher", None)
    if dispatcher is None:
        dispatcher = make_dispatcher(settings=settings)

    arxiv_scope = McpToolScope(tool_name="arxiv")
    scopes: dict[str, McpToolScope] = {"arxiv": arxiv_scope}
    mcp = McpClient(scopes=scopes, dispatcher=dispatcher)

    # --- Step 1: get_abstract → metadata ---
    meta_result = await mcp.call(
        McpInvocation(
            tool="arxiv",
            method="get_abstract",
            args=ArxivGetAbstractArgs(paper_id=arxiv_id),
        )
    )
    # The dispatcher merges parsed JSON into the result dict; try "result" key first
    raw_meta = str(meta_result.get("result", "{}"))
    meta = _parse_arxiv_abstract_response(raw_meta, arxiv_id)

    # --- Step 2: download_paper → markdown content ---
    dl_result = await mcp.call(
        McpInvocation(
            tool="arxiv",
            method="download_paper",
            args=ArxivDownloadPaperArgs(paper_id=arxiv_id),
        )
    )
    # Upstream returns {status, message, paper_id, source, content}
    # content is the paper's full text in markdown
    markdown_content: str = str(dl_result.get("content", dl_result.get("result", "")))

    # --- Step 3: save markdown to workspace_root/papers/<arxiv_id>.md ---
    papers_dir = workspace_root / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    md_path = papers_dir / f"{arxiv_id}.md"
    md_path.write_text(markdown_content, encoding="utf-8")

    # Validate the saved path is inside workspace_root (path-traversal guard)
    try:
        md_path.resolve().relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Markdown file path {md_path} is outside workspace root {workspace_root}"
        ) from exc

    sha256 = _compute_sha256(md_path)

    # --- Step 4: chunk (directly on markdown — GROBID skipped in Phase A) ---
    paper_id = uuid4()
    chunks = list(chunk_text(paper_id, markdown_content))

    # --- Step 5: embed ---
    embedder = _get_embedder(request, settings)
    chunk_texts = [c.text for c in chunks]
    embeddings: list[list[float]] = embedder.embed(chunk_texts) if chunk_texts else []

    # --- Step 6: persist paper + chunks to SQLite ---
    added_at = datetime.now(UTC).isoformat()
    with connect(settings.db_path) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO papers "
                "(id, arxiv_id, doi, title, authors_json, year, abstract,"
                " pdf_path, sha256, added_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(paper_id),
                    arxiv_id,
                    meta["title"],
                    json.dumps(meta["authors"]),
                    meta["year"],
                    meta["abstract"],
                    str(md_path),
                    sha256,
                    added_at,
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

    # --- Step 7: persist vectors ---
    chroma_path = settings.chroma_path or (workspace_root / "chroma")
    store = ChromaVectorStore(chroma_path)
    vectors: list[ChunkVector] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        metadata: dict[str, str | int | float | bool] = {"text": chunk.text}
        if chunk.section is not None:
            metadata["section"] = chunk.section
        if chunk.page is not None:
            metadata["page"] = chunk.page
        if chunk.char_start is not None:
            metadata["char_start"] = chunk.char_start
        if chunk.char_end is not None:
            metadata["char_end"] = chunk.char_end
        vectors.append(
            ChunkVector(
                chunk_id=chunk.id,
                paper_id=paper_id,
                embedding=embedding,
                metadata=metadata,
            )
        )
    store.add(vectors)

    # --- Step 8: return Paper model ---
    return Paper(
        id=paper_id,
        arxiv_id=arxiv_id,
        doi=None,
        title=meta["title"],
        authors=meta["authors"],
        year=meta["year"],
        abstract=meta["abstract"],
        pdf_path=str(md_path),
        sha256=sha256,
        primary_topic=None,
        added_at=datetime.fromisoformat(added_at),
    )
