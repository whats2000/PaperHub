"""Papers REST surface (SRS v2.3, FR-08). Backs the deterministic UI
gestures; the Research Agent uses research_tools dispatchers instead."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, model_validator

from paperhub.agents.research_tools import (
    NoIngestibleSourceError,
    _to_fts5_query,
    add_paper_to_session_dispatch,
)
from paperhub.api.deps import get_chroma, get_llm
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.pipelines.paper_pipeline import (
    ArxivMetadata,
    IngestRequest,
    PaperPipeline,
)

_PDF_MIME = "application/pdf"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers", tags=["papers"])


class IngestBody(BaseModel):
    session_id: int
    paper_id: str | None = None  # preferred: "arxiv:<id>" | "library:<pc_id>"
    arxiv_id: str | None = None  # legacy alias
    # Optional metadata-from-source — lets the caller (frontend or an MCP
    # client) supply title/abstract/authors/year so the backend doesn't have
    # to re-fetch from arXiv just to populate paper_content.
    # Per Bug 1 follow-up M2: any caller that already has the metadata
    # (Search results, library browse, etc.) should send it.
    title: str | None = None
    abstract: str | None = None
    authors: list[str] | None = None
    year: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> IngestBody:
        if not (self.paper_id or self.arxiv_id):
            raise ValueError("provide paper_id or arxiv_id")
        if self.paper_id and self.arxiv_id:
            raise ValueError("provide only one of paper_id / arxiv_id")
        return self


class ReferenceItem(BaseModel):
    papers_id: int
    paper_content_id: int
    enabled: bool
    added_at: str
    arxiv_id: str | None
    title: str
    year: int | None
    kind: str


class IngestResponse(BaseModel):
    paper_content_id: int
    papers_id: int
    cache_hit: bool
    title: str


class FromLibraryBody(BaseModel):
    session_id: int
    paper_content_id: int


class PatchBody(BaseModel):
    enabled: bool


class LibraryItem(BaseModel):
    paper_content_id: int
    arxiv_id: str | None
    title: str
    abstract: str | None
    year: int | None


@router.get("", response_model=list[ReferenceItem])
async def list_session_references(
    session_id: int = Query(..., ge=1),
) -> list[ReferenceItem]:
    """List papers attached to a session, joined to paper_content."""
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT p.id, p.paper_content_id, p.enabled, p.added_at, "
            "       pc.arxiv_id, pc.title, pc.year, pc.kind "
            "FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
            "WHERE p.session_id = ? ORDER BY p.added_at DESC",
            (session_id,),
        ) as cur,
    ):
        rows = await cur.fetchall()
    return [
        ReferenceItem(
            papers_id=int(r[0]),
            paper_content_id=int(r[1]),
            enabled=bool(r[2]),
            added_at=str(r[3]),
            arxiv_id=r[4],
            title=str(r[5] or ""),
            year=int(r[6]) if r[6] is not None else None,
            kind=str(r[7]),
        )
        for r in rows
    ]


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_paper(body: IngestBody, request: Request) -> IngestResponse:
    """Ingest a paper. Accepts paper_id (preferred) or legacy arxiv_id.
    Cache-aware: second call with the same identifier returns cache_hit=True.
    Optional title/abstract/authors/year fields are forwarded as
    metadata_override so the arxiv: path can skip the arXiv metadata
    API call when the caller already has the metadata (M2 fix)."""
    # Normalise: if legacy arxiv_id supplied, convert to paper_id format.
    paper_id = body.paper_id or f"arxiv:{body.arxiv_id}"

    # Build metadata_override from optional fields when the caller supplies them.
    # Only applies to the arxiv: branch (the dispatcher ignores it for ss: and
    # library: prefixes — SS metadata is authoritative for ss:, and library:
    # doesn't ingest).
    metadata_override: ArxivMetadata | None = None
    if body.title is not None:
        metadata_override = ArxivMetadata(
            title=body.title,
            abstract=body.abstract or "",
            authors=body.authors or [],
            year=body.year,
        )

    settings = load_settings()
    try:
        async with open_db(settings.db_path) as conn:
            pipeline = PaperPipeline(
                conn,
                papers_cache_dir=settings.papers_cache_dir,
                chroma=get_chroma(request, settings),
            )
            result = await add_paper_to_session_dispatch(
                paper_id,
                pipeline=pipeline,
                conn=conn,
                session_id=body.session_id,
                metadata_override=metadata_override,
            )
    except NoIngestibleSourceError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "no_ingestible_source",
                "title": exc.title,
                "paper_id": exc.paper_id,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IngestResponse(
        paper_content_id=result.paper_content_id,
        papers_id=result.papers_id,
        cache_hit=result.cache_hit,
        title=result.title,
    )


@router.post("/upload", response_model=IngestResponse, status_code=201)
async def upload_paper(
    request: Request,
    session_id: int = Form(..., ge=1),
    file: UploadFile = File(...),  # noqa: B008 — FastAPI parameter declaration idiom
    title: str | None = Form(None),
) -> IngestResponse:
    """Accept a multipart PDF upload, sha256-key it, run the pipeline.

    Bypasses ``add_paper_to_session_dispatch`` because that function is
    paper_id-string-keyed (``arxiv:`` / ``ss:`` / ``library:`` prefixes);
    file bytes don't belong in the LLM-visible tool surface. Calls
    ``PaperPipeline.ingest()`` directly with an upload_path IngestRequest.

    Streams the body in 1 MiB blocks (Starlette has already spooled the
    multipart body to a temp file under the hood, so the in-memory
    guarantee is bounded by Starlette's SpooledTemporaryFile threshold,
    not us) and enforces the PAPERHUB_MAX_UPLOAD_MB ceiling mid-stream,
    so we never write past the byte cap on disk.
    """
    settings = load_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    if file.content_type != _PDF_MIME:
        raise HTTPException(
            415,
            f"unsupported content_type={file.content_type!r}; "
            f"expected {_PDF_MIME}",
        )

    # Stream to a tempdir, preserving the client-supplied filename so the
    # pipeline's title fallback (``upload_path.stem``) and cache-filename
    # derivation (``cache_dir / upload_path.name``) reflect the upload
    # rather than an opaque ``tmpXXXX`` token. We sandbox via tempdir +
    # Path.name to strip any path components the client might inject.
    tmpdir = tempfile.mkdtemp(prefix="paperhub-upload-")
    safe_name = Path(file.filename or "upload.pdf").name or "upload.pdf"
    upload_path = Path(tmpdir) / safe_name
    bytes_written = 0
    try:
        with upload_path.open("wb") as out:
            while chunk := await file.read(1 << 20):  # 1 MiB blocks
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise HTTPException(
                        413,
                        f"file exceeds {settings.max_upload_mb} MiB ceiling",
                    )
                out.write(chunk)

        # Honor an optional caller-supplied title so the library row doesn't
        # inherit the (often opaque) filename stem. Whitespace-only / missing
        # title falls through to the pipeline's existing `upload_path.stem`
        # fallback. Authors/year/abstract are intentionally NOT exposed here
        # — that's a later PATCH surface.
        metadata_override: ArxivMetadata | None = None
        if title is not None and title.strip():
            metadata_override = ArxivMetadata(
                title=title.strip(),
                abstract="",
                authors=[],
                year=None,
            )

        async with open_db(settings.db_path) as conn:
            pipeline = PaperPipeline(
                conn,
                papers_cache_dir=settings.papers_cache_dir,
                chroma=get_chroma(request, settings),
                llm=get_llm(request),
                title_extract_model=settings.router_model,
            )
            result = await pipeline.ingest(
                IngestRequest(
                    session_id=session_id,
                    upload_path=upload_path,
                    upload_kind="pdf",
                    metadata_override=metadata_override,
                ),
            )
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError as exc:
            logger.warning(
                "failed to remove upload tempdir %s: %s", tmpdir, exc,
            )

    return IngestResponse(
        paper_content_id=result.paper_content_id,
        papers_id=result.papers_id,
        cache_hit=result.cache_hit,
        title=result.title,
    )


@router.get("/library", response_model=list[LibraryItem])
async def list_library(
    session_id: int = Query(...),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LibraryItem]:
    """Indexed paper_content rows NOT already in `session_id`.

    Optional `q` filters on title and abstract using FTS5 MATCH — supports
    multi-word queries with Google-style AND semantics.
    """
    where = ["pc.id NOT IN (SELECT paper_content_id FROM papers WHERE session_id = ?)"]
    args: list[int | str] = [session_id]
    if q:
        fts_query = _to_fts5_query(q)
        if fts_query:
            where.append(
                "EXISTS (SELECT 1 FROM paper_content_fts fts "
                "WHERE fts.rowid = pc.id AND paper_content_fts MATCH ?)"
            )
            args.append(fts_query)
    sql = (
        "SELECT pc.id, pc.arxiv_id, pc.title, pc.abstract, pc.year "
        f"FROM paper_content pc WHERE {' AND '.join(where)} "
        "ORDER BY pc.year DESC NULLS LAST, pc.id DESC "
        "LIMIT ? OFFSET ?"
    )
    args.extend([limit, offset])
    settings = load_settings()
    async with open_db(settings.db_path) as conn, conn.execute(sql, args) as cur:
        rows = await cur.fetchall()
    return [
        LibraryItem(
            paper_content_id=int(r[0]),
            arxiv_id=r[1],
            title=r[2] or "",
            abstract=r[3],
            year=int(r[4]) if r[4] is not None else None,
        )
        for r in rows
    ]


@router.post("/from-library", response_model=IngestResponse)
async def attach_from_library(body: FromLibraryBody) -> IngestResponse:
    """Idempotent on UNIQUE(session_id, paper_content_id). Re-attach returns
    the existing `papers` row instead of erroring."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        # Confirm paper_content exists.
        async with conn.execute(
            "SELECT title FROM paper_content WHERE id = ?",
            (body.paper_content_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(
                404, f"paper_content {body.paper_content_id} not found"
            )
        title = row[0] or ""
        await conn.execute(
            "INSERT OR IGNORE INTO papers (session_id, paper_content_id) VALUES (?, ?)",
            (body.session_id, body.paper_content_id),
        )
        await conn.commit()
        async with conn.execute(
            "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
            (body.session_id, body.paper_content_id),
        ) as cur:
            papers_row = await cur.fetchone()
        if papers_row is None:
            raise HTTPException(
                500, "papers row missing after INSERT — DB invariant violated"
            )
    return IngestResponse(
        paper_content_id=body.paper_content_id,
        papers_id=int(papers_row[0]),
        cache_hit=True,
        title=title,
    )


@router.patch("/{papers_id}", response_model=dict[str, bool])
async def toggle_enabled(papers_id: int, body: PatchBody) -> dict[str, bool]:
    """Toggle the `enabled` flag on a session↔paper membership row."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        cur = await conn.execute(
            "UPDATE papers SET enabled = ? WHERE id = ?",
            (1 if body.enabled else 0, papers_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, f"papers row {papers_id} not found")
        await conn.commit()
    return {"enabled": body.enabled}


@router.delete("/{papers_id}", status_code=204)
async def remove_from_session(papers_id: int) -> None:
    """Removes the membership row only — `paper_content` (and its chunks +
    Chroma vectors + cached on-disk artefacts) are untouched, so re-attaching
    later is a cache hit."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        cur = await conn.execute("DELETE FROM papers WHERE id = ?", (papers_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, f"papers row {papers_id} not found")
        await conn.commit()


class DocumentModeResponse(BaseModel):
    mode: str  # "html" | "pdf"


@router.get("/content/{paper_content_id}/document", response_model=DocumentModeResponse)
async def document_mode(paper_content_id: int) -> DocumentModeResponse:
    """Return whether this paper should be viewed as HTML or PDF.

    A top-level ``*.pdf`` in ``source_dir_path`` means the HTML was rendered
    from a PDF (PyMuPDF) and is visually broken — the Citation Canvas should
    show the original PDF instead.  If no top-level PDF exists the paper was
    rendered from LaTeX (pandoc) and HTML is fine.

    Note: uses top-level glob only (not rglob) so figure PDFs inside a
    ``source/`` subdirectory of a LaTeX paper don't false-positive.
    """
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT source_dir_path FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, f"paper_content {paper_content_id} not found")
    source_dir = Path(row[0])
    # Top-level glob only — rglob would pick up figure PDFs inside source/ subdirs
    # of a LaTeX paper, making them false-positive as PDF-rendered.
    pdfs = sorted(source_dir.glob("*.pdf"))  # noqa: ASYNC240
    mode = "pdf" if pdfs else "html"
    return DocumentModeResponse(mode=mode)


@router.get("/content/{paper_content_id}/pdf")
async def serve_pdf(paper_content_id: int) -> FileResponse:
    """Serve the original PDF file inline so the Citation Canvas can display
    it in an iframe for PDF-rendered papers (arXiv-PDF-fallback and pdf_upload).

    Resolution order:
    1. If ``source_path`` ends with ``.pdf`` (case-insensitive) and exists on
       disk, serve it directly.
    2. Otherwise, fall back to the first top-level ``*.pdf`` in
       ``source_dir_path`` (sorted for determinism).
    3. If neither yields a file, raise 404.
    """
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT source_path, source_dir_path FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"paper_content {paper_content_id} not found")
    source_path, source_dir_path = row[0], row[1]

    pdf_path: Path | None = None
    # 1. Prefer source_path when it is itself the PDF.
    if source_path and Path(source_path).suffix.lower() == ".pdf":
        candidate = Path(source_path)
        if candidate.is_file():  # noqa: ASYNC240
            pdf_path = candidate
    # 2. Fallback: first top-level *.pdf in source_dir (top-level only, not rglob).
    if pdf_path is None and source_dir_path:
        candidates = sorted(Path(source_dir_path).glob("*.pdf"))  # noqa: ASYNC240
        if candidates:
            pdf_path = candidates[0]

    if pdf_path is None:
        raise HTTPException(
            404,
            f"no PDF file found for paper_content {paper_content_id}",
        )
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
        content_disposition_type="inline",
    )


@router.get("/content/{paper_content_id}/html")
async def serve_html(paper_content_id: int) -> FileResponse:
    """Served as a file to keep the Citation Canvas in Plan D simple
    (just point an iframe / fetch at this URL)."""
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT html_path FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, f"no html for paper_content {paper_content_id}")
    path = Path(row[0])
    # Sync stat is acceptable here: Plan D Citation Canvas serves cached on-disk HTML.
    # Wrapping in asyncio.to_thread is deferred (same scope decision as paper_pipeline.py).
    if not path.is_file():  # noqa: ASYNC240
        raise HTTPException(410, f"html_path on disk missing: {path}")
    return FileResponse(path, media_type="text/html")


@router.get("/content/{paper_content_id}/asset/{asset_path:path}")
async def serve_asset(paper_content_id: int, asset_path: str) -> FileResponse:
    """Serve a figure (or other static asset) referenced by ``source.html`` by
    its path relative to the paper's ``source_dir_path``.

    Figures are no longer base64-inlined into ``source.html`` (a 70MB inline
    HTML OOM'd the Citation Canvas iframe — arxiv:2605.02881). The renderer
    rewrites each ``<img>`` to a relative ``asset/<path>`` URL; because the
    iframe loads the HTML from ``/papers/content/{id}/html``, the browser
    resolves those to this route and fetches each figure lazily as a file.

    Path-traversal is blocked: the resolved target must stay inside
    ``source_dir_path``."""
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT source_dir_path FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, f"no source dir for paper_content {paper_content_id}")
    # Sync path ops are acceptable here (same scope decision as serve_html/serve_pdf).
    base_dir = Path(row[0]).resolve()  # noqa: ASYNC240
    target = (base_dir / asset_path).resolve()  # noqa: ASYNC240
    # Containment guard — refuse any ../ escape outside the paper's cache dir.
    if base_dir != target and base_dir not in target.parents:
        raise HTTPException(400, "asset path escapes paper directory")
    if not target.is_file():  # noqa: ASYNC240
        raise HTTPException(404, f"asset not found: {asset_path}")
    media_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=media_type or "application/octet-stream")


@router.delete("/content/{paper_content_id}", status_code=204)
async def delete_library_paper(
    paper_content_id: int,
    request: Request,
    force: bool = Query(
        False, description="If true, also remove any papers (session-membership) rows referencing this paper_content."
    ),
) -> None:
    """Purge a paper from the library entirely — paper_content row, chunks
    (cascade), Chroma vectors, and the on-disk cache directory.

    Test-friendly destructive endpoint. The proper UX (with batch ops,
    cascading session warnings, and probably an undo window) belongs in a
    later phase when user-driven uploads land. For now this is the smallest
    surface that lets a tester wipe a bad ingest and re-attach cleanly.

    Without `?force=true`, refuses (409) if any session still references the
    paper — operators must DELETE /papers/{papers_id} first OR pass force.
    With force, the dispatcher cascade-deletes membership rows itself
    (schema is ON DELETE RESTRICT on `papers.paper_content_id`).
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        async with conn.execute(
            "SELECT source_dir_path FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(
                404, f"paper_content {paper_content_id} not found",
            )
        source_dir_path = row[0]

        async with conn.execute(
            "SELECT COUNT(*) FROM papers WHERE paper_content_id = ?",
            (paper_content_id,),
        ) as cur:
            count_row = await cur.fetchone()
        ref_count = int(count_row[0]) if count_row else 0

        if ref_count > 0 and not force:
            raise HTTPException(
                409,
                detail={
                    "error": "in_use_by_sessions",
                    "session_count": ref_count,
                    "hint": "pass ?force=true to cascade-delete the memberships, or DELETE /papers/{papers_id} for each first.",
                },
            )

        # Cascade membership rows when force is set (schema is RESTRICT).
        if ref_count > 0:
            await conn.execute(
                "DELETE FROM papers WHERE paper_content_id = ?",
                (paper_content_id,),
            )

        # chunks → ON DELETE CASCADE handles them when paper_content is removed.
        await conn.execute(
            "DELETE FROM paper_content WHERE id = ?", (paper_content_id,),
        )
        await conn.commit()

    # Chroma vectors — best-effort; log on failure so DB stays consistent.
    try:
        chroma = get_chroma(request, settings)
        chroma.delete_paper(paper_content_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chroma delete failed for paper_content_id=%s: %s",
            paper_content_id, exc,
        )

    # On-disk cache — best-effort. paper_content.source_dir_path stores
    # the per-paper cache dir itself (e.g. workspace/papers_cache/arxiv/
    # 2510.10274/) — NOT the source/ subdir, despite the misleading
    # name. Earlier this code took `.parent` thinking it was at
    # source/ level; that lifted up to workspace/papers_cache/arxiv/
    # and rmtree wiped EVERY paper's cache in one shot (production
    # bug: deleting one paper destroyed the whole library on disk).
    # Now we delete exactly the per-paper dir and nothing above it.
    if source_dir_path:
        await asyncio.to_thread(_purge_paper_cache_dir, source_dir_path)


def _purge_paper_cache_dir(source_dir_path: str) -> None:
    """Best-effort rmtree of a single paper's cache dir.

    Defence-in-depth: refuses to delete if the path doesn't look like
    a per-paper cache dir (must contain ``papers_cache`` AND end in a
    stem that isn't itself a parent name like ``arxiv`` / ``upload`` /
    empty). Logs a warning and bails on anything suspicious so a
    schema-semantics drift can't accidentally rm-tree the whole tree.
    Runs synchronously — caller wraps in ``asyncio.to_thread`` to
    keep the FastAPI event loop responsive.
    """
    cache_dir = Path(source_dir_path)
    if not (cache_dir.exists() and cache_dir.is_dir()):
        return
    if "papers_cache" not in cache_dir.parts or cache_dir.name in (
        "papers_cache", "arxiv", "upload", "",
    ):
        logger.warning(
            "refusing to rmtree suspicious source_dir_path=%s "
            "(would have wiped a parent directory)", source_dir_path,
        )
        return
    try:
        shutil.rmtree(cache_dir)
    except OSError as exc:
        logger.warning(
            "failed to remove cache dir %s: %s", cache_dir, exc,
        )


# Re-export Pydantic models for test introspection (kept here for locality).
__all__ = [
    "router",
    "IngestBody",
    "IngestResponse",
    "ReferenceItem",
    "FromLibraryBody",
    "PatchBody",
    "LibraryItem",
]
