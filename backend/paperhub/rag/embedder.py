"""Embedder wrapper for the RAG pipeline.

Production: wraps :class:`sentence_transformers.SentenceTransformer`.
Tests: use :class:`FakeEmbedder` which returns deterministic hashed vectors
       so tests don't download the real model or need GPU.
"""

from __future__ import annotations

from typing import Protocol


class EmbedderProtocol(Protocol):
    """Minimum interface every embedder must satisfy."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Embedder:
    """Production embedder backed by ``sentence-transformers``.

    Parameters
    ----------
    model_name:
        HuggingFace model ID.  Default is ``BAAI/bge-small-en-v1.5`` per
        settings (fast, high-quality asymmetric retrieval encoder, 384-dim).

    The actual ``SentenceTransformer`` model is constructed lazily on the
    first ``embed()`` call — construction can mmap multi-GB safetensors and
    on memory-constrained Windows boxes will raise OS error 1455 ("paging
    file too small") if attempted eagerly. Lazy load means non-RAG code
    paths (chitchat, /health, /papers/import metadata-only) don't pay the
    cost and don't risk the failure.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model: object | None = None  # populated on first embed()

    def _ensure_loaded(self) -> object:
        if self._model is None:
            # Import lazily so unit tests that use FakeEmbedder never load the
            # sentence-transformers heavy dependency.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per text (length == model dimension)."""
        # sentence-transformers has no py.typed; encode() return type is Any.
        from typing import cast

        model = self._ensure_loaded()
        raw = model.encode(texts, convert_to_numpy=True)  # type: ignore[attr-defined]
        return cast(list[list[float]], raw.tolist())


class FakeEmbedder:
    """Deterministic test double — no model download, no GPU.

    Produces 384-dimensional vectors using a hash-seeded pattern.  The pattern
    places most weight on dimensions whose index shares a residue with
    ``hash(text) % DIM``, so vectors for different texts point in genuinely
    different directions in cosine space (unlike the naïve all-equal design,
    which would make every vector collinear).

    Two texts with the same hash will have identical vectors (cosine sim = 1.0).
    Texts with different hashes will have cosine sim < 1.0.
    """

    DIM = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for t in texts:
            h = hash(t) % self.DIM
            # Spike at position h; all others are small constants
            vec = [0.01] * self.DIM
            vec[h] = 1.0
            result.append(vec)
        return result
