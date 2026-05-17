"""Tests for the vector-store driver interface (Chroma default backend)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from paperhub.data.vectors import ChromaVectorStore, ChunkVector, VectorSearchHit


def _vec(seed: float) -> list[float]:
    return [
        seed,
        seed + 0.1,
        seed + 0.2,
        seed + 0.3,
        seed + 0.4,
        seed + 0.5,
        seed + 0.6,
        seed + 0.7,
    ]


@pytest.fixture()
def store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(path=tmp_path / "chroma")


def test_add_then_search_returns_hits(store: ChromaVectorStore) -> None:
    paper_id = uuid4()
    chunk_id = uuid4()
    store.add(
        [
            ChunkVector(
                chunk_id=chunk_id,
                paper_id=paper_id,
                embedding=_vec(0.0),
                metadata={"section": "intro", "page": 1},
            ),
        ]
    )
    hits = store.search(query_embedding=_vec(0.0), top_k=5)
    assert len(hits) == 1
    assert hits[0].chunk_id == chunk_id
    assert hits[0].paper_id == paper_id
    assert isinstance(hits[0], VectorSearchHit)


def test_delete_by_paper_removes_vectors(store: ChromaVectorStore) -> None:
    paper_id = uuid4()
    store.add(
        [
            ChunkVector(chunk_id=uuid4(), paper_id=paper_id, embedding=_vec(0.0), metadata={}),
            ChunkVector(chunk_id=uuid4(), paper_id=paper_id, embedding=_vec(1.0), metadata={}),
        ]
    )
    store.delete_by_paper(paper_id)
    hits = store.search(query_embedding=_vec(0.0), top_k=5)
    assert hits == []


def test_search_filters_by_paper_id(store: ChromaVectorStore) -> None:
    p1, p2 = uuid4(), uuid4()
    c1, c2 = uuid4(), uuid4()
    store.add(
        [
            ChunkVector(chunk_id=c1, paper_id=p1, embedding=_vec(0.0), metadata={}),
            ChunkVector(chunk_id=c2, paper_id=p2, embedding=_vec(0.0), metadata={}),
        ]
    )
    hits = store.search(query_embedding=_vec(0.0), top_k=5, paper_ids=[p1])
    assert [h.chunk_id for h in hits] == [c1]
