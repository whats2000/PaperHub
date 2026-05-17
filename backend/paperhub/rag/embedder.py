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
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        # Import lazily so unit tests that use FakeEmbedder never load the
        # sentence-transformers heavy dependency.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per text (length == model dimension)."""
        # sentence-transformers has no py.typed; encode() return type is Any.
        # We request numpy output so .tolist() gives list[list[float]]; the cast
        # is required because mypy cannot verify the no-stubs return type.
        from typing import cast

        raw = self._model.encode(texts, convert_to_numpy=True)
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
