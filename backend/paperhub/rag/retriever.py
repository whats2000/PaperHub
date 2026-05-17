"""Two-stage RAG retriever: vector search → optional cross-encoder rerank.

Phase A ships the vector-search stage only (reranker deferred to Phase B).
The ``reranker`` parameter is accepted for forward-compatibility but is
documented as a no-op in this release.

Corpus-size-aware candidate pool
---------------------------------
To avoid returning too few candidates to the optional reranker (Phase B),
the first stage fetches ``min(50, ceil(corpus_count / 3))`` vectors instead
of the raw ``top_k``.  The final list is then truncated to ``top_k`` when
no reranker is configured.
"""

from __future__ import annotations

import math
from uuid import UUID

from pydantic import BaseModel

from paperhub.data.models import Chunk
from paperhub.data.vectors import ChromaVectorStore, VectorStore
from paperhub.rag.embedder import EmbedderProtocol


class RetrievedChunk(BaseModel):
    """A chunk retrieved from the vector store with its similarity score."""

    chunk: Chunk
    score: float


class Retriever:
    """Embed a query, search the vector store, optionally rerank.

    Parameters
    ----------
    vector_store:
        Any object satisfying the :class:`~paperhub.data.vectors.VectorStore`
        protocol (``ChromaVectorStore`` in production).
    embedder:
        Any object with ``embed(texts) -> list[list[float]]``.
    reranker:
        **Not implemented in Phase A.**  Pass ``None`` (default).  Phase B
        will accept a cross-encoder callable here.
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: EmbedderProtocol,
        reranker: object = None,
    ) -> None:
        self._store = vector_store
        self._embedder = embedder
        self._reranker = reranker  # reserved for Phase B

    def _corpus_count(self) -> int:
        """Return the number of vectors in the store (best-effort)."""
        # ChromaVectorStore exposes .count() via the underlying chromadb collection.
        # For other VectorStore implementations that don't have .count(), default to 0
        # so the formula degrades gracefully to min(50, 1) == 1.
        if isinstance(self._store, ChromaVectorStore):
            return self._store._coll.count()  # internal access for corpus size
        return 0

    def search(
        self,
        query: str,
        top_k: int = 5,
        paper_ids: list[UUID] | None = None,
    ) -> list[RetrievedChunk]:
        """Return up to *top_k* chunks most relevant to *query*.

        The two-stage funnel:

        1. Embed the query.
        2. Fetch ``n_results = min(50, ceil(corpus_count / 3))`` candidates
           from the vector store (but at least *top_k*).
        3. If a reranker is configured (Phase B), rerank to *top_k*;
           otherwise return the top *top_k* by vector score directly.
        """
        (query_embedding,) = self._embedder.embed([query])

        corpus_count = self._corpus_count()
        if corpus_count:
            candidate_count = max(top_k, min(50, math.ceil(corpus_count / 3)))
        else:
            candidate_count = top_k

        hits = self._store.search(
            query_embedding=query_embedding,
            top_k=candidate_count,
            paper_ids=paper_ids,
        )

        if self._reranker is not None:
            # Phase B hook — not implemented yet.
            raise NotImplementedError("cross-encoder reranking is a Phase B feature")

        # No reranker: return top top_k by vector score (already sorted by store)
        top_hits = hits[:top_k]

        results: list[RetrievedChunk] = []
        for hit in top_hits:
            chunk = Chunk(
                id=hit.chunk_id,
                paper_id=hit.paper_id,
                section=str(hit.metadata.get("section")) if "section" in hit.metadata else None,
                page=int(hit.metadata["page"]) if "page" in hit.metadata else None,
                char_start=int(hit.metadata["char_start"])
                if "char_start" in hit.metadata
                else None,
                char_end=int(hit.metadata["char_end"]) if "char_end" in hit.metadata else None,
                text=str(hit.metadata.get("text", "")),
            )
            results.append(RetrievedChunk(chunk=chunk, score=hit.score))

        return results
