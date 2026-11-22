"""Papers REST surface (SRS v2.3, FR-08). Backs the deterministic UI
gestures; the Research Agent uses research_tools dispatchers instead."""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import mimetypes
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import aiosqlite
import pymupdf
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from paperhub.agents.research_tools import (
    NoIngestibleSourceError,
    add_paper_to_session_dispatch,
)
from paperhub.api.deps import get_llm
from paperhub.config import load_settings
from paperhub.db.connection import open_db, write_transaction
from paperhub.ideation.domains import assign_color_indices
from paperhub.notes.locate import locate_text
from paperhub.outbound.destinations import OutboundBlocked
from paperhub.pipelines.page_pdf import crop_png, is_compiled_pdf, page_pdf_path
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    read_block_assets,
    read_paper_asset,
)
from paperhub.pipelines.paper_pipeline import (
    ArxivMetadata,
    IngestRequest,
    PaperPipeline,
)
from paperhub.pipelines.sentinels import figure_dom_id
from paperhub.translation.models import ParagraphsResponse
from paperhub.translation.pdf_paragraphs import build_paragraphs

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
    is_private: bool


class IngestResponse(BaseModel):
    paper_content_id: int
    papers_id: int
    cache_hit: bool
    title: str
    is_private: bool = False


async def _read_is_private(conn: aiosqlite.Connection, paper_content_id: int) -> bool:
    """Server truth for the lock marker (the flag is upgrade-only)."""
    async with conn.execute(
        "SELECT is_private FROM paper_content WHERE id = ?",
        (paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    return bool(row[0]) if row else False


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
    is_private: bool


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
            "       pc.arxiv_id, pc.title, pc.year, pc.kind, pc.is_private "
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
            is_private=bool(r[8]),
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
            )
            result = await add_paper_to_session_dispatch(
                paper_id,
                pipeline=pipeline,
                conn=conn,
                session_id=body.session_id,
                metadata_override=metadata_override,
                unpaywall_email=settings.unpaywall_email,
            )
            is_private = await _read_is_private(conn, result.paper_content_id)
    except NoIngestibleSourceError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "no_ingestible_source",
                "title": exc.title,
                "paper_id": exc.paper_id,
                "tried_urls": exc.tried_urls,
            },
        ) from exc
    except OutboundBlocked as exc:
        raise HTTPException(
            status_code=403,
            detail={"detail": "destination_blocked", "destination": exc.destination},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IngestResponse(
        paper_content_id=result.paper_content_id,
        papers_id=result.papers_id,
        cache_hit=result.cache_hit,
        title=result.title,
        is_private=is_private,
    )


@router.post("/upload", response_model=IngestResponse, status_code=201)
async def upload_paper(
    request: Request,
    session_id: int = Form(..., ge=1),
    file: UploadFile = File(...),  # noqa: B008 — FastAPI parameter declaration idiom
    title: str | None = Form(None),
    private: bool = Form(False),
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
                llm=get_llm(request),
                title_extract_model=settings.router_model,
            )
            result = await pipeline.ingest(
                IngestRequest(
                    session_id=session_id,
                    upload_path=upload_path,
                    upload_kind="pdf",
                    metadata_override=metadata_override,
                    is_private=private,
                ),
            )
            if private and result.cache_hit:
                # A cache miss INSERTs the row already private (no public
                # window). A cache hit upgrades only — a re-upload without
                # the flag never clears it, so 0 is never written here.
                async with write_transaction(conn):
                    await conn.execute(
                        "UPDATE paper_content SET is_private = 1 WHERE id = ?",
                        (result.paper_content_id,),
                    )
            is_private = await _read_is_private(conn, result.paper_content_id)
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
        is_private=is_private,
    )


def _title_rank(title: str, tokens: list[str]) -> int:
    """0: every (normalized) token starts a word of the title; 1: the
    normalized title holds every token; 2: neither (an abstract match)."""
    from paperhub.db.connection import norm_for_match

    words = [norm_for_match(w) for w in re.split(r"[^\w]+", title)]
    tail = ["".join(words[i:]) for i in range(len(words))]
    if all(any(t.startswith(tok) for t in tail if t) for tok in tokens):
        return 0
    whole = norm_for_match(title)
    return 1 if all(tok in whole for tok in tokens) else 2


@router.get("/library", response_model=list[LibraryItem])
async def list_library(
    session_id: int = Query(...),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LibraryItem]:
    """Indexed paper_content rows NOT already in `session_id`.

    Optional `q` filters on title and abstract using **normalized
    case-insensitive substring match**: both sides are run through
    ``paperhub_norm`` (lowercase + drop non-alphanumerics) before the
    LIKE. So ``imus`` finds ``OptimusVLA``, ``prio`` finds ``Prior``,
    AND ``xvla`` finds ``X-VLA: Soft-Prompted Transformer…`` (the
    hyphen+colon would have blocked the substring otherwise). Tokens
    are whitespace-split and AND-joined across title+abstract. The
    LLM-driven path (``search_library_dispatch``) keeps FTS5 keyword
    semantics; this endpoint is human-facing, where forgiving substring
    is expected. With `q`, a title where every token starts a word ranks
    first, then a title holding every token, then the rest; each newest
    first.
    """
    where = ["pc.id NOT IN (SELECT paper_content_id FROM papers WHERE session_id = ?)"]
    args: list[int | str] = [session_id]
    tokens: list[str] = []
    if q:
        from paperhub.db.connection import norm_for_match

        for tok in q.split():
            normalized = norm_for_match(tok)
            if not normalized:
                # User typed only punctuation in this token (e.g. '-' or
                # '%') — skip rather than match-everything via '%%'.
                continue
            pattern = f"%{normalized}%"
            # paperhub_norm() runs on each row in title/abstract for this
            # filter. At the library's expected scale (≪10k papers) the
            # per-row Python callback is well under a millisecond total.
            where.append(
                "(paperhub_norm(pc.title) LIKE ? "
                "OR paperhub_norm(pc.abstract) LIKE ?)"
            )
            args.extend([pattern, pattern])
            tokens.append(normalized)
    sql = (
        "SELECT pc.id, pc.arxiv_id, pc.title, pc.abstract, pc.year, pc.is_private "
        f"FROM paper_content pc WHERE {' AND '.join(where)} "
        "ORDER BY pc.year DESC NULLS LAST, pc.id DESC"
    )
    if not tokens:
        sql += " LIMIT ? OFFSET ?"
        args.extend([limit, offset])
    settings = load_settings()
    async with open_db(settings.db_path) as conn, conn.execute(sql, args) as cur:
        rows = list(await cur.fetchall())
    if tokens:
        # A paper named by the query must not sink below newer ones that
        # only mention it ("[[LoRA" vs "...an ExpLORAtion..."); the sort is
        # stable, so each tier stays newest first.
        rows.sort(key=lambda r: _title_rank(r[2] or "", tokens))
        rows = rows[offset:offset + limit]
    return [
        LibraryItem(
            paper_content_id=int(r[0]),
            arxiv_id=r[1],
            title=r[2] or "",
            abstract=r[3],
            year=int(r[4]) if r[4] is not None else None,
            is_private=bool(r[5]),
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
            "INSERT INTO papers (session_id, paper_content_id) VALUES (?, ?) "
            "ON CONFLICT (session_id, paper_content_id) DO UPDATE SET enabled = 1",
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
        is_private = await _read_is_private(conn, body.paper_content_id)
    return IngestResponse(
        paper_content_id=body.paper_content_id,
        papers_id=int(papers_row[0]),
        cache_hit=True,
        title=title,
        is_private=is_private,
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
    # The paper has a page PDF (its own, or one compiled from its LaTeX):
    # [view:] citations open on it (a PDF tab for HTML papers).
    page_pdf: bool = False


@router.get("/content/{paper_content_id}/sections")
async def list_paper_sections(paper_content_id: int) -> list[str]:
    """Return the paper's section names in document order — the deterministic
    section list for the per-slide Sources editor's picker. Sections with no
    name (NULL) are skipped; order is by first appearance (``char_start``)."""
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT section FROM chunks WHERE paper_content_id = ? "
            "AND section IS NOT NULL AND section != '' "
            "GROUP BY section ORDER BY MIN(char_start)",
            (paper_content_id,),
        ) as cur,
    ):
        rows = await cur.fetchall()
    return [str(r[0]) for r in rows]


class PaperInfo(BaseModel):
    id: int
    title: str


@router.get("/content/{paper_content_id}/info", response_model=PaperInfo)
async def paper_info(paper_content_id: int) -> PaperInfo:
    """A library paper's current title (a note's ``[[`` link follows it);
    404 once the paper is gone from the library."""
    async with (
        open_db(load_settings().db_path) as conn,
        conn.execute(
            "SELECT title FROM paper_content WHERE id = ?", (paper_content_id,)
        ) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"paper_content {paper_content_id} not found")
    return PaperInfo(id=paper_content_id, title=str(row[0] or ""))


class SectionRange(BaseModel):
    first_chunk_id: int
    last_chunk_id: int


@router.get("/content/{paper_content_id}/section-range", response_model=SectionRange)
async def section_range(paper_content_id: int, section: str) -> SectionRange:
    """The first and last chunk of a section (named as ``/sections`` lists
    them), in document order: the reader opens a ``[[`` / ``@`` link to
    the section as a citation of that range. 404 for an unknown section."""
    async with (
        open_db(load_settings().db_path) as conn,
        conn.execute(
            "SELECT id FROM chunks WHERE paper_content_id = ? AND section = ? "
            "ORDER BY char_start, id",
            (paper_content_id, section),
        ) as cur,
    ):
        ids = [int(r[0]) for r in await cur.fetchall()]
    if not ids:
        raise HTTPException(404, f"section {section!r} not found")
    return SectionRange(first_chunk_id=ids[0], last_chunk_id=ids[-1])


class ChunkAnchor(BaseModel):
    id: int
    dom_id: str
    section: str | None


@router.get("/content/{paper_content_id}/chunk-anchors")
async def list_chunk_anchors(paper_content_id: int) -> list[ChunkAnchor]:
    """The chunks with a ``phchunk-N`` span in the rendered HTML, in chunk
    order: the reader maps a selection to chunk ids and its section (a
    mark's anchor and its note section) from the nearest span."""
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT id, dom_id, section FROM chunks WHERE paper_content_id = ? "
            "AND dom_id IS NOT NULL ORDER BY id",
            (paper_content_id,),
        ) as cur,
    ):
        rows = await cur.fetchall()
    return [ChunkAnchor(id=int(r[0]), dom_id=str(r[1]),
                        section=str(r[2]) if r[2] else None) for r in rows]


class LocateIn(BaseModel):
    # A long selection is matched by its head and tail (notes/locate.py);
    # this only bounds the request.
    text: str = Field(max_length=200_000)
    # The page text just around the selection: which occurrence it is.
    before: str = Field("", max_length=2_000)
    after: str = Field("", max_length=2_000)


class LocateMatch(BaseModel):
    chunk_start: int
    chunk_end: int
    chunk_ids: list[int]
    section: str | None
    text_start: int        # span in the reading text (the HTML reader's)
    text_end: int
    #: The reading-text paragraph it sits in (None: it crosses a break).
    paragraph: int | None = None


class LocateOut(BaseModel):
    match: LocateMatch | None
    #: How the match was decided (words compared, which runs, why a miss).
    meta: dict[str, Any] = Field(serialization_alias="_meta")


@router.post("/content/{paper_content_id}/locate", response_model_by_alias=True)
async def locate(paper_content_id: int, body: LocateIn) -> LocateOut:
    """Where text read off the LaTeX-compiled PDF of an HTML paper sits in
    its reading text: the chunks, the section and the span; ``match`` is
    null when it is not found. A PDF mark there files under that section,
    and Follow reports it. The decision rides in ``_meta`` and the log."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        found, meta = await locate_text(conn, paper_content_id, body.text,
                                        before=body.before, after=body.after)
    logger.info("locate paper=%s words=%s matched=%s reason=%s context=%s ambiguous=%s",
                paper_content_id, meta.get("query_words"), meta.get("matched"),
                meta.get("reason"), meta.get("context"), meta.get("ambiguous"))
    return LocateOut(match=LocateMatch(**found) if found else None, meta=meta)


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
    return DocumentModeResponse(mode=mode, page_pdf=page_pdf_path(source_dir) is not None)


@router.get("/content/{paper_content_id}/paragraphs", response_model=ParagraphsResponse)
async def pdf_paragraphs(paper_content_id: int) -> ParagraphsResponse:
    """Translatable PDF paragraphs (with geometry) built from Marker blocks (Plan H1)."""
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT source_dir_path FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"paper_content {paper_content_id} not found")
    got = read_block_assets(Path(row[0])) if row[0] else None
    if got is None:
        return ParagraphsResponse(status="no_blocks", paragraphs=[])
    blocks, inline_math_llm = got
    return ParagraphsResponse(
        status="ready" if inline_math_llm else "no_inline_math_llm",
        paragraphs=build_paragraphs(blocks),
    )


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


class FigureMeta(BaseModel):
    paper_content_id: int
    id: str
    caption: str
    page: int | None           # 0-based, on the page PDF
    title: str
    has_image: bool
    # Where a [fig:] citation opens: the HTML anchor the render put before
    # the figure, and its box (PDF points) on the page PDF.
    html_anchor: str | None = None
    bbox: list[float] | None = None


async def _figure(paper_content_id: int, figure_id: str) -> tuple[FigureAsset, Path, str]:
    """A paper's asset figure by id, its image path and the paper title; 404
    when the paper or the figure is unknown. The id is matched against the
    asset's own list, so a crafted id never reaches the filesystem."""
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT source_dir_path, title FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, f"paper_content {paper_content_id} not found")
    src = Path(row[0])
    asset = read_paper_asset(src)
    fig = next((f for f in (asset.figures if asset else []) if f.id == figure_id), None)
    if fig is None:
        raise HTTPException(404, f"no figure {figure_id!r} in paper {paper_content_id}")
    return fig, fig.abs_image_path(src), str(row[1] or "")


@router.get("/content/{paper_content_id}/figures/{figure_id}")
async def figure_meta(paper_content_id: int, figure_id: str) -> FigureMeta:
    """Caption and page of a figure a chat answer cites as ``[fig:P/ID]``."""
    fig, path, title = await _figure(paper_content_id, figure_id)
    return FigureMeta(
        paper_content_id=paper_content_id, id=fig.id, caption=fig.caption,
        page=fig.page, title=title, has_image=path.is_file(),  # noqa: ASYNC240
        html_anchor=await asyncio.to_thread(_html_anchor, paper_content_id, fig.id),
        bbox=list(fig.bbox) if fig.bbox is not None else None,
    )


def _html_anchor(paper_content_id: int, figure_id: str) -> str | None:
    """``phfig-NNN`` when the paper's rendered HTML carries that anchor."""
    dom = figure_dom_id(figure_id)
    if dom is None:
        return None
    settings = load_settings()
    with sqlite3.connect(settings.db_path) as conn:
        row = conn.execute("SELECT html_path FROM paper_content WHERE id = ?",
                           (paper_content_id,)).fetchone()
    html = Path(row[0]) if row and row[0] else None
    if html is None or not html.is_file():
        return None
    return dom if f'id="{dom}"' in html.read_text(encoding="utf-8", errors="ignore") else None


async def _cache_dir(paper_content_id: int) -> Path:
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute("SELECT source_dir_path FROM paper_content WHERE id = ?",
                     (paper_content_id,)) as cur,
    ):
        row = await cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, f"paper_content {paper_content_id} not found")
    return Path(row[0])


@router.get("/content/{paper_content_id}/page-pdf")
async def serve_page_pdf(paper_content_id: int) -> FileResponse:
    """The paper's page PDF: what the agent viewed and [view:] citations open."""
    pdf = page_pdf_path(await _cache_dir(paper_content_id))
    if pdf is None:
        raise HTTPException(404, f"paper_content {paper_content_id} has no page PDF")
    return FileResponse(pdf, media_type="application/pdf", content_disposition_type="inline")


class ViewRegion(BaseModel):
    page: int                  # 0-based, as the PDF reader takes it
    bbox: list[float]          # PDF points, top-left origin
    source: str                # "own" (the paper's PDF) | "compiled" (asset/paper.pdf)


@router.get("/content/{paper_content_id}/view-region")
async def view_region(paper_content_id: int, page: int, box: str) -> ViewRegion:
    """A ``[view:P/N/box]`` citation's region in PDF points on the page PDF."""
    try:
        vals = [int(v) for v in box.split(",")]
    except ValueError:
        vals = []
    if (len(vals) != 4 or not (0 <= vals[0] < vals[2] <= 1000)
            or not (0 <= vals[1] < vals[3] <= 1000)):
        raise HTTPException(400, "box must be x0,y0,x1,y1 in 0-1000 with x0<x1, y0<y1")
    pdf = page_pdf_path(await _cache_dir(paper_content_id))
    if pdf is None:
        raise HTTPException(404, f"paper_content {paper_content_id} has no page PDF")
    size = await asyncio.to_thread(_page_rect, pdf, page)
    if size is None:
        raise HTTPException(404, f"page {page} is not in the page PDF")
    w, h = size
    return ViewRegion(
        page=page - 1,
        bbox=[vals[0] / 1000 * w, vals[1] / 1000 * h, vals[2] / 1000 * w, vals[3] / 1000 * h],
        source="compiled" if is_compiled_pdf(pdf) else "own",
    )


def _page_rect(pdf: Path, page: int) -> tuple[float, float] | None:
    with pymupdf.open(pdf) as doc:  # type: ignore[no-untyped-call]
        if not 1 <= page <= doc.page_count:
            return None
        r = doc[page - 1].rect
        return float(r.width), float(r.height)


@router.get("/content/{paper_content_id}/page-crop")
async def page_crop(paper_content_id: int, page: int, x0: float, y0: float,
                    x1: float, y1: float) -> Response:
    """A region of a page (fractions, 0-based page) as a PNG, for a region mark."""
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise HTTPException(400, "box must be fractions with x0<x1, y0<y1 in 0-1")
    pdf = page_pdf_path(await _cache_dir(paper_content_id))
    if pdf is None:
        raise HTTPException(404, f"paper_content {paper_content_id} has no page PDF")
    try:
        png = await asyncio.to_thread(crop_png, pdf, page, (x0, y0, x1, y1))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if png is None:
        raise HTTPException(404, f"page {page} is not in the page PDF")
    return Response(png, media_type="image/png")


class PageBlock(BaseModel):
    block_id: str
    type: str
    page: int                  # 0-based
    box: list[float]           # page fractions x0,y0,x1,y1
    text: str
    section: str | None = None  # the section the block sits in


@router.get("/content/{paper_content_id}/blocks")
async def page_blocks(paper_content_id: int, page: int) -> list[PageBlock]:
    """The reading blocks of one page (from ``blocks.json``), boxes as page
    fractions, with their section: where a PDF mark files, and what the
    reader's Follow reports. Empty when the paper has none."""
    cache = await _cache_dir(paper_content_id)
    got = read_block_assets(cache)
    if got is None:
        return []
    on_page = [b for b in got[0] if b.page == page]
    pdf = page_pdf_path(cache)
    size = await asyncio.to_thread(_page_rect, pdf, page + 1) if pdf else None
    if not on_page or size is None:
        return []
    w, h = size
    out: list[PageBlock] = []
    for b in on_page:
        x0, y0, x1, y1 = b.bbox
        if x1 <= x0 or y1 <= y0:
            continue  # no geometry
        out.append(PageBlock(
            block_id=b.block_id, type=b.block_type, page=b.page,
            box=[max(0.0, x0 / w), max(0.0, y0 / h),
                 min(1.0, x1 / w), min(1.0, y1 / h)],
            text=_plain_text(b.html), section=b.section))
    return out


def _plain_text(html: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", "", html)).split())


@router.get("/content/{paper_content_id}/figures/{figure_id}/image")
async def figure_image(paper_content_id: int, figure_id: str) -> FileResponse:
    """The figure's image file, for the ``[fig:P/ID]`` citation popover."""
    _fig, path, _title = await _figure(paper_content_id, figure_id)
    if not path.is_file():  # noqa: ASYNC240
        raise HTTPException(404, f"figure {figure_id!r} has no image file")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, content_disposition_type="inline")


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
    # Path-traversal barrier on the RAW user-supplied component, BEFORE it is
    # joined into a filesystem path. We split on BOTH separators and reject any
    # `..` segment, an absolute root, or a Windows drive — so the value can no
    # longer reference anything outside the paper's cache dir. (The earlier
    # realpath+startswith form was a correct RUNTIME defence, but the path-
    # injection analysis did not accept it as a barrier — the alerts stayed open;
    # an explicit `..`/absolute reject on the raw input is the recognised form.)
    segments = asset_path.replace("\\", "/").split("/")
    if ".." in segments or asset_path.startswith(("/", "\\")) or ":" in asset_path:
        raise HTTPException(400, "asset path escapes paper directory")
    safe_segments = [s for s in segments if s and s != "."]
    base_dir = Path(str(row[0])).resolve()  # noqa: ASYNC240
    target = base_dir.joinpath(*safe_segments)
    # Defence in depth: a symlink INSIDE the cache must not redirect outside it.
    resolved = target.resolve()  # noqa: ASYNC240
    if resolved != base_dir and base_dir not in resolved.parents:
        raise HTTPException(400, "asset path escapes paper directory")
    if not target.is_file():  # noqa: ASYNC240
        raise HTTPException(404, f"asset not found: {asset_path}")
    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=media_type or "application/octet-stream")


@router.delete("/content/{paper_content_id}", status_code=204)
async def delete_library_paper(
    paper_content_id: int,
    force: bool = Query(
        False, description="If true, also remove any papers (session-membership) rows referencing this paper_content."
    ),
) -> None:
    """Purge a paper from the library entirely — paper_content row, chunks
    (cascade), and the on-disk cache directory.

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
        # Its domain tags cascaded away: free their colour slots (Plan H3 —
        # the ideation graph only reads colours, writes assign them).
        await assign_color_indices(conn)

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
