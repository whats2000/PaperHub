"""Tests for the two-stage RAG retriever."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from paperhub.data.models import Chunk
from paperhub.data.vectors import ChromaVectorStore, ChunkVector
from paperhub.rag.embedder import FakeEmbedder
from paperhub.rag.retriever import Retriever


def _fake_embed(text: str) -> list[float]:
    """Mirror FakeEmbedder logic so we can compute expected similarity."""
    h = hash(text) % FakeEmbedder.DIM
    vec = [0.01] * FakeEmbedder.DIM
    vec[h] = 1.0
    return vec


@pytest.fixture()
def store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(path=tmp_path / "chroma")


@pytest.fixture()
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


def _seed_chunk(
    store: ChromaVectorStore,
    paper_id: UUID,
    text: str,
) -> Chunk:
    chunk_id = uuid4()
    emb = _fake_embed(text)
    store.add(
        [
            ChunkVector(
                chunk_id=chunk_id,
                paper_id=paper_id,
                embedding=emb,
                metadata={"text": text},
            )
        ]
    )
    return Chunk(
        id=chunk_id,
        paper_id=paper_id,
        section=None,
        page=None,
        char_start=None,
        char_end=None,
        text=text,
    )


def test_search_returns_most_similar_chunk(
    store: ChromaVectorStore, embedder: FakeEmbedder
) -> None:
    """The retriever returns the chunk whose FakeEmbedder vector best matches."""
    paper_id = uuid4()
    query = "machine learning"
    # Seed two chunks: one whose hash matches the query's hash (identical),
    # and one that is different.
    _seed_chunk(store, paper_id, query)  # identical text → identical embedding
    _seed_chunk(store, paper_id, "unrelated content xyz")

    retriever = Retriever(store, embedder)
    results = retriever.search(query, top_k=1)

    assert len(results) == 1
    # The retrieved chunk's text must be the one matching the query
    assert results[0].chunk.text == query


def test_retriever_corpus_size_cap(store: ChromaVectorStore, embedder: FakeEmbedder) -> None:
    """n_results is capped to the corpus count, not top_k."""
    paper_id = uuid4()
    _seed_chunk(store, paper_id, "alpha")
    _seed_chunk(store, paper_id, "beta")

    retriever = Retriever(store, embedder)
    # top_k=10 but collection only has 2 vectors — must not raise
    results = retriever.search("alpha", top_k=10)
    assert len(results) == 2


def test_retriever_filters_by_paper_ids(store: ChromaVectorStore, embedder: FakeEmbedder) -> None:
    """paper_ids filter is forwarded to the vector store."""
    p1, p2 = uuid4(), uuid4()
    query = "deep learning"
    _seed_chunk(store, p1, query)
    _seed_chunk(store, p2, "something else entirely")

    retriever = Retriever(store, embedder)
    results = retriever.search(query, top_k=5, paper_ids=[p1])

    assert all(r.chunk.paper_id == p1 for r in results)
