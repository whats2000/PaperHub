"""Vector-store driver behind a narrow typed interface.

Phase A ships only the Chroma backend (default per SRS v1.6). The
sqlite-vec opt-in alternative is added in a later phase. Both implement
the same `add` / `search` / `delete_by_paper` contract so agent code
never sees the backend choice.

Uses chromadb 1.x API (PersistentClient + cosine-space HNSW collection).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import chromadb
from chromadb.config import Settings as ChromaSettings
from pydantic import BaseModel

VectorBackendName = Literal["chroma", "sqlite-vec"]


class ChunkVector(BaseModel):
    chunk_id: UUID
    paper_id: UUID
    embedding: list[float]
    metadata: dict[str, str | int | float | bool]


class VectorSearchHit(BaseModel):
    chunk_id: UUID
    paper_id: UUID
    score: float
    metadata: dict[str, str | int | float | bool]


class VectorStore(Protocol):
    def add(self, vectors: list[ChunkVector]) -> None: ...
    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        paper_ids: list[UUID] | None = None,
    ) -> list[VectorSearchHit]: ...
    def delete_by_paper(self, paper_id: UUID) -> None: ...


class ChromaVectorStore:
    """Local persistent Chroma backend (chromadb 1.x)."""

    _COLLECTION = "chunks"

    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
        )
        self._coll = self._client.get_or_create_collection(
            name=self._COLLECTION, metadata={"hnsw:space": "cosine"}
        )

    def add(self, vectors: list[ChunkVector]) -> None:
        if not vectors:
            return
        self._coll.add(
            ids=[str(v.chunk_id) for v in vectors],
            embeddings=[v.embedding for v in vectors],  # type: ignore[arg-type]
            metadatas=[{"paper_id": str(v.paper_id), **v.metadata} for v in vectors],
        )

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        paper_ids: list[UUID] | None = None,
    ) -> list[VectorSearchHit]:
        where: dict[str, object] | None = None
        if paper_ids:
            where = {"paper_id": {"$in": [str(pid) for pid in paper_ids]}}
        res = self._coll.query(
            query_embeddings=[query_embedding],  # type: ignore[arg-type]
            n_results=top_k,
            where=where,  # type: ignore[arg-type]
        )
        hits: list[VectorSearchHit] = []
        ids_outer = res.get("ids") or [[]]
        dists_outer = res.get("distances") or [[]]
        metas_outer = res.get("metadatas") or [[]]
        for chunk_id_s, dist, meta in zip(
            ids_outer[0], dists_outer[0], metas_outer[0], strict=False
        ):
            meta_dict = dict(meta or {})
            paper_id_s = str(meta_dict.pop("paper_id"))
            hits.append(
                VectorSearchHit(
                    chunk_id=UUID(chunk_id_s),
                    paper_id=UUID(paper_id_s),
                    score=1.0 - float(dist),  # cosine distance → similarity
                    metadata={
                        k: v
                        for k, v in meta_dict.items()
                        if isinstance(v, str | int | float | bool)
                    },
                )
            )
        return hits

    def delete_by_paper(self, paper_id: UUID) -> None:
        self._coll.delete(where={"paper_id": str(paper_id)})
