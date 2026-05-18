"""Papers REST surface (SRS v2.3, FR-08). Backs the deterministic UI
gestures; the Research Agent uses research_tools dispatchers instead."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, model_validator

from paperhub.agents.research_tools import (
    NoIngestibleSourceError,
    _to_fts5_query,
    add_paper_to_session_dispatch,
)
from paperhub.api.deps import get_chroma
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.pipelines.paper_pipeline import PaperPipeline

router = APIRouter(prefix="/papers", tags=["papers"])


class IngestBody(BaseModel):
    session_id: int
    paper_id: str | None = None  # preferred: "arxiv:<id>" | "library:<pc_id>"
    arxiv_id: str | None = None  # legacy alias

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
    Cache-aware: second call with the same identifier returns cache_hit=True."""
    # Normalise: if legacy arxiv_id supplied, convert to paper_id format.
    paper_id = body.paper_id or f"arxiv:{body.arxiv_id}"

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
