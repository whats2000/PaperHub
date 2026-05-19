"""Reranker factory + implementations.

Mirrors :mod:`paperhub.pipelines.embedder`:

* ``_HttpReranker`` — POSTs to the model server (default, production).
* ``_CrossEncoderReranker`` — loads CrossEncoder in-process. Selected
  when ``Settings.inprocess_models`` is set (typically tests).

Both implement the ``Reranker`` Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx
from sentence_transformers import CrossEncoder

from paperhub.config import Settings, load_settings
from paperhub.pipelines._device import resolve_device


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


@runtime_checkable
class Reranker(Protocol):
    """Public interface for all reranker implementations."""

    def rerank(self, query: str, texts: list[str], top_k: int) -> list[RerankResult]:
        ...


# ─────────────────────────── in-process ────────────────────────────


class _CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: CrossEncoder | None = None

    def _load(self) -> CrossEncoder:
        if self._model is None:
            self._model = CrossEncoder(self._model_name, device=resolve_device())
        return self._model

    def rerank(self, query: str, texts: list[str], top_k: int) -> list[RerankResult]:
        if not texts:
            return []
        model = self._load()
        # CrossEncoder.predict accepts list[str | list[str]] at runtime despite
        # strict type stubs; cast suppresses the variance mismatch.
        pairs: list[str | list[str]] = [[query, t] for t in texts]
        scores = model.predict(pairs)  # type: ignore[arg-type]
        ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)
        return [RerankResult(index=i, score=float(s)) for i, s in ranked[:top_k]]


# ─────────────────────────── HTTP client ───────────────────────────


_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class _HttpReranker:
    """Thin HTTP client over the model server's ``/rerank`` endpoint."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=_HTTP_TIMEOUT)

    def rerank(self, query: str, texts: list[str], top_k: int) -> list[RerankResult]:
        if not texts:
            return []
        resp = self._client.post(
            f"{self._base_url}/rerank",
            json={"query": query, "texts": texts, "top_k": top_k},
        )
        resp.raise_for_status()
        data = resp.json()
        indices = data.get("indices") or []
        scores = data.get("scores") or []
        return [
            RerankResult(index=int(i), score=float(s))
            for i, s in zip(indices, scores, strict=False)
        ]


# ─────────────────────────── factory ───────────────────────────────


_singleton: Reranker | None = None


def get_reranker() -> Reranker:
    """Process-wide reranker singleton. Mirrors :func:`get_embedder`."""
    global _singleton
    if _singleton is None:
        settings = load_settings()
        _singleton = _build_reranker(settings)
    return _singleton


def _build_reranker(settings: Settings) -> Reranker:
    if settings.inprocess_models:
        return _CrossEncoderReranker(settings.reranker_model)
    base_url = f"http://{settings.model_server_host}:{settings.model_server_port}"
    return _HttpReranker(base_url)


def reset_singleton() -> None:
    """Test helper — clear the cached reranker so the next call
    re-runs the factory."""
    global _singleton
    _singleton = None
