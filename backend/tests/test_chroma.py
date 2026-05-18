from pathlib import Path

import numpy as np

from paperhub.rag.chroma import ChromaStore


def test_add_then_search_returns_matching_chunks(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path)
    vecs = np.random.RandomState(42).randn(3, 384).astype(np.float32)
    # Normalize so cosine-sim behaves.
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    store.add_chunks(
        paper_content_id=1,
        chunk_ids=[10, 11, 12],
        texts=["alpha", "beta", "gamma"],
        embeddings=vecs,
    )

    results = store.search(query_embedding=vecs[0], paper_content_ids=[1], k=2)
    assert len(results) == 2
    # First match should be the query itself.
    assert results[0].chunk_id == 10
    assert results[0].text == "alpha"


def test_search_filters_by_paper_content_id(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path)
    vecs = np.random.RandomState(0).randn(2, 384).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    store.add_chunks(1, [10], ["paper1"], vecs[:1])
    store.add_chunks(2, [20], ["paper2"], vecs[1:])

    results = store.search(query_embedding=vecs[1], paper_content_ids=[1], k=5)
    assert len(results) == 1
    assert results[0].chunk_id == 10  # Only paper 1 returned despite paper 2 being closer.
