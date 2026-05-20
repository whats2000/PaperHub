"""Chunk-resolution surface (SRS v2.13, FR-03 Citation Canvas).

The canvas resolves a `[chunk:<id>]` click to the data it needs to
text-search the paper's rendered HTML: which paper to load
(`paper_content_id`) and what passage to find (`text`). Read-only.
"""
from __future__ import annotations

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


@router.get("/{chunk_id}", response_model=ChunkResolution)
async def get_chunk(chunk_id: int) -> ChunkResolution:
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT id, paper_content_id, section, text FROM chunks WHERE id = ?",
            (chunk_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"no chunk {chunk_id}")
    return ChunkResolution(
        id=int(row[0]),
        paper_content_id=int(row[1]),
        section=row[2],
        text=row[3],
    )
