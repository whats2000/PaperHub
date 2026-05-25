"""Chunk-resolution surface (SRS v2.13, FR-03 Citation Canvas).

The canvas resolves a `[chunk:<id>]` click to the data it needs to locate the
passage in the paper's rendered HTML: which paper to load (`paper_content_id`),
a deterministic anchor (`dom_id` → `getElementById`, when the ingest-time
sentinel survived) and the passage `text` (the text-search fallback). Read-only.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from paperhub.config import load_settings
from paperhub.db.connection import open_db

router = APIRouter(prefix="/chunks", tags=["chunks"])


class ChunkResolution(BaseModel):
    id: int
    paper_content_id: int
    section: str | None
    text: str
    dom_id: str | None
    match_text: str | None
    # F2.1 A2': Marker block provenance — page index + union bbox
    # ([x0,y0,x1,y1]) for a geometric Citation Canvas highlight. NULL for
    # non-Marker (LaTeX / PyMuPDF) chunks.
    page: int | None
    bbox: list[float] | None


@router.get("/{chunk_id}", response_model=ChunkResolution)
async def get_chunk(chunk_id: int) -> ChunkResolution:
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT id, paper_content_id, section, text, dom_id, match_text, "
            "page, bbox "
            "FROM chunks WHERE id = ?",
            (chunk_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"no chunk {chunk_id}")
    bbox_raw = row[7]
    bbox = json.loads(bbox_raw) if bbox_raw is not None else None
    return ChunkResolution(
        id=int(row[0]),
        paper_content_id=int(row[1]),
        section=row[2],
        text=row[3],
        dom_id=row[4],
        match_text=row[5],
        page=row[6],
        bbox=bbox,
    )
