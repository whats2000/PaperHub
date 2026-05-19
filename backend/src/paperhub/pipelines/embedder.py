"""Embedder factory + implementations.

Two implementations:

* ``_HttpEmbedder`` — POSTs to the model server (see
  ``paperhub.modelserver``). This is the default and is what runs in
  production / dev. The model lives outside the uvicorn worker so
  ``--reload`` on backend code doesn't reset the weights.

* ``_SentenceTransformersEmbedder`` — loads SentenceTransformer in-
  process. Selected when ``Settings.inprocess_models`` is true (env
  ``PAPERHUB_INPROCESS_MODELS=1``). Used by tests and by environments
  where the operator can't run a second process.

Both implement the ``Embedder`` Protocol. The ``get_embedder()``
factory caches a process-wide singleton so the HTTP client's httpx
connection pool is reused across requests.
"""
from __future__ import annotations

from typing import Protocol

import httpx
import numpy as np
from sentence_transformers import SentenceTransformer

from paperhub.config import Settings, load_settings
from paperhub.pipelines._device import resolve_device


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        ...


# ─────────────────────────── in-process ────────────────────────────


class _SentenceTransformersEmbedder:
    """Load the model in this process. Used when
    ``Settings.inprocess_models`` is set — typically tests."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: SentenceTransformer | None = None

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(
                self._model_name, device=resolve_device(),
            )
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vecs, dtype=np.float32)


# ─────────────────────────── HTTP client ───────────────────────────


# Generous: the underlying call can be slow on the first /embed when
# the model server lazily loads weights, and on CPU-only boxes with
# large batches. The client never retries — failures propagate so the
# Paper Pipeline's caller surfaces a real error.
_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=5.0)


class _HttpEmbedder:
    """Thin HTTP client over the model server's ``/embed`` endpoint.

    Keeps a single :class:`httpx.Client` for connection pooling. We
    use the synchronous client because the Paper Pipeline calls
    ``embed()`` from sync code paths (chunk → embed → persist) inside
    an ``async`` method; switching the Embedder Protocol to async
    would ripple changes through several call sites.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=_HTTP_TIMEOUT)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        resp = self._client.post(
            f"{self._base_url}/embed", json={"texts": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        vecs = data.get("vectors") or []
        return np.asarray(vecs, dtype=np.float32)


# ─────────────────────────── factory ───────────────────────────────


_singleton: Embedder | None = None


def get_embedder() -> Embedder:
    """Return the process-wide embedder singleton.

    First call constructs either the HTTP client (default) or the in-
    process SentenceTransformer (when ``Settings.inprocess_models``
    is set).
    """
    global _singleton
    if _singleton is None:
        settings = load_settings()
        _singleton = _build_embedder(settings)
    return _singleton


def _build_embedder(settings: Settings) -> Embedder:
    if settings.inprocess_models:
        return _SentenceTransformersEmbedder(settings.embedding_model)
    base_url = f"http://{settings.model_server_host}:{settings.model_server_port}"
    return _HttpEmbedder(base_url)


def reset_singleton() -> None:
    """Test helper: clear the cached embedder so the next ``get_embedder``
    call re-runs the factory. Used by tests that toggle
    ``PAPERHUB_INPROCESS_MODELS`` at runtime."""
    global _singleton
    _singleton = None
