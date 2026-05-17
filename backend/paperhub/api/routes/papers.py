"""POST /papers/import — import a paper from arXiv into PaperHub.

Pipeline:
1. Call arxiv MCP ``fetch_metadata`` → extract title, authors, year, abstract.
2. Call arxiv MCP ``download_pdf`` → get local PDF path.
3. Validate PDF path is inside workspace_root.
4. Call grobid MCP ``process_fulltext`` (falls back to stub on error).
5. Extract text from TEI XML (or fallback plain text).
6. Chunk via ``chunker.chunk_text``.
7. Embed via ``Embedder(settings.embedding_model)`` (lazy).
8. Insert ``papers`` + ``chunks`` rows into SQLite.
9. Insert vectors into ChromaVectorStore.
10. Return the created ``Paper`` Pydantic model.

Phase A limitations:
- Metadata parsing is best-effort (arXiv JSON response varies).
- PDF SHA-256 is computed from the file bytes.
- PDF text fallback uses the TEI ``<p>`` tag text content.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from paperhub.config import Settings, get_settings
from paperhub.data.db import connect
from paperhub.data.models import Paper
from paperhub.data.vectors import ChromaVectorStore, ChunkVector
from paperhub.mcp.client import McpClient
from paperhub.mcp.launchers import make_dispatcher
from paperhub.mcp.scopes import (
    ArxivDownloadPdfArgs,
    ArxivFetchMetadataArgs,
    GrobidProcessFulltextArgs,
    McpInvocation,
    McpToolScope,
)
from paperhub.rag.chunker import chunk_text
from paperhub.rag.embedder import Embedder

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


def _extract_text_from_tei(tei_xml: str) -> str:
    """Extract plain text from TEI XML, falling back to stripping tags."""
    try:
        # Strip namespace for simpler XPath
        xml_no_ns = re.sub(r' xmlns="[^"]+"', "", tei_xml)
        root = ET.fromstring(xml_no_ns)
        parts: list[str] = []
        for elem in root.iter():
            if elem.tag in {"p", "head", "title"} and elem.text:
                parts.append(elem.text.strip())
        if parts:
            return "\n\n".join(parts)
    except ET.ParseError:
        pass
    # Fallback: strip all XML tags
    return re.sub(r"<[^>]+>", " ", tei_xml)


def _parse_arxiv_metadata(raw: str, arxiv_id: str) -> dict[str, Any]:
    """Parse arXiv metadata JSON (best-effort) into a dict of known fields."""
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


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/papers/import", response_model=Paper)
async def import_paper(
    request: PaperImportRequest,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Paper:
    """Import a paper from arXiv into PaperHub.

    Parameters
    ----------
    request.arxiv_id:
        The arXiv identifier (e.g. ``"2301.07041"``).
    """
    arxiv_id = request.arxiv_id.strip()
    workspace_root = settings.workspace_root

    # Build a minimal dispatcher + scoped MCP client
    dispatcher = make_dispatcher()
    arxiv_scope = McpToolScope(tool_name="arxiv")
    grobid_scope = McpToolScope(tool_name="grobid", filesystem_root=workspace_root)
    scopes: dict[str, McpToolScope] = {"arxiv": arxiv_scope, "grobid": grobid_scope}
    mcp = McpClient(scopes=scopes, dispatcher=dispatcher)

    # --- Step 1: fetch metadata ---
    meta_result = await mcp.call(
        McpInvocation(
            tool="arxiv",
            method="fetch_metadata",
            args=ArxivFetchMetadataArgs(arxiv_id=arxiv_id),
        )
    )
    raw_meta = str(meta_result.get("result", "{}"))
    meta = _parse_arxiv_metadata(raw_meta, arxiv_id)

    # --- Step 2: download PDF ---
    pdf_result = await mcp.call(
        McpInvocation(
            tool="arxiv",
            method="download_pdf",
            args=ArxivDownloadPdfArgs(arxiv_id=arxiv_id),
        )
    )
    pdf_path_raw = str(pdf_result.get("result", ""))
    pdf_path = Path(pdf_path_raw)

    # Validate the PDF path is under workspace_root
    try:
        pdf_path.resolve().relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Downloaded PDF path {pdf_path} is outside workspace root {workspace_root}"
        ) from exc

    sha256 = _compute_sha256(pdf_path)

    # --- Step 3: process with GROBID ---
    tei_result = await mcp.call(
        McpInvocation(
            tool="grobid",
            method="process_fulltext",
            args=GrobidProcessFulltextArgs(pdf_path=pdf_path),
        )
    )
    tei_xml = str(tei_result.get("tei", ""))
    full_text = _extract_text_from_tei(tei_xml)

    # --- Step 4: chunk ---
    paper_id = uuid4()
    chunks = list(chunk_text(paper_id, full_text))

    # --- Step 5: embed ---
    embedder = Embedder(settings.embedding_model)
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
                    str(pdf_path),
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
        pdf_path=str(pdf_path),
        sha256=sha256,
        primary_topic=None,
        added_at=datetime.fromisoformat(added_at),
    )
