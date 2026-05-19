"""FastAPI app exposing embedder + reranker over HTTP.

Runs as a sibling process to the main backend. Endpoints:

  GET  /health           — liveness probe (used by spawn TCP-then-HTTP wait)
  POST /embed            — body: {"texts": [str, ...]} → {"vectors": [[float, ...]]}
  POST /rerank           — body: {"query": str, "texts": [str, ...], "top_k": int}
                           → {"indices": [int, ...], "scores": [float, ...]}

Models are lazily loaded on first request that needs them — embedder
on /embed, reranker on /rerank. Once loaded they stay resident for the
process lifetime. Both endpoints accept empty ``texts`` and short-circuit
without touching the model (cheap probe for cache warmth checks).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder, SentenceTransformer

from paperhub.config import load_settings
from paperhub.pipelines._device import resolve_device

_LOG = logging.getLogger(__name__)

# Process-wide singletons. Loaded lazily on first request.
_embed_model: SentenceTransformer | None = None
_rerank_model: CrossEncoder | None = None


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    # Row-major: vectors[i] is the embedding for texts[i].
    vectors: list[list[float]]


class RerankRequest(BaseModel):
    query: str
    texts: list[str]
    top_k: int


class RerankResponse(BaseModel):
    # indices[i] is the position in the input texts of the i-th best hit.
    # scores[i] is the cross-encoder score for that hit.
    indices: list[int]
    scores: list[float]


app = FastAPI(title="paperhub-modelserver")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "embed_loaded": _embed_model is not None,
        "rerank_loaded": _rerank_model is not None,
    }


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    if not req.texts:
        return EmbedResponse(vectors=[])
    model = _get_embed_model()
    vecs = model.encode(
        req.texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return EmbedResponse(vectors=vecs.tolist())


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    if not req.texts:
        return RerankResponse(indices=[], scores=[])
    model = _get_rerank_model()
    pairs: list[str | list[str]] = [[req.query, t] for t in req.texts]
    # CrossEncoder.predict accepts list[str | list[str]] at runtime
    # despite the strict stub; cast suppresses the mismatch.
    scores = model.predict(pairs)  # type: ignore[arg-type]
    ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)
    top = ranked[: req.top_k]
    return RerankResponse(
        indices=[i for i, _ in top],
        scores=[float(s) for _, s in top],
    )


def _get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        settings = load_settings()
        device = resolve_device()
        _LOG.info(
            "modelserver loading embedder model=%s device=%s",
            settings.embedding_model, device,
        )
        _embed_model = SentenceTransformer(
            settings.embedding_model, device=device,
        )
    return _embed_model


def _get_rerank_model() -> CrossEncoder:
    global _rerank_model
    if _rerank_model is None:
        settings = load_settings()
        device = resolve_device()
        _LOG.info(
            "modelserver loading reranker model=%s device=%s",
            settings.reranker_model, device,
        )
        _rerank_model = CrossEncoder(
            settings.reranker_model, device=device,
        )
    return _rerank_model
