"""Isolated process hosting the sentence-transformers embedder and the
cross-encoder reranker.

Why a separate process: the backend's FastAPI worker is run under
``uvicorn --reload`` during development, so any edit to a file in
``src/paperhub/`` re-imports the entire module graph and resets every
module-level singleton — including the embedder's ~110 MB
SentenceTransformer weights and the reranker's ~80 MB CrossEncoder
weights. The next ingest pays the full reload tax (and if another
reload fires mid-load, the in-flight HTTP download to arxiv aborts).

The model server lives outside the worker process, so reloads can't
touch it. The worker becomes a thin HTTP client (see
``pipelines.embedder._HttpEmbedder`` and ``rag.reranker._HttpReranker``).
"""

from paperhub.modelserver.server import app

__all__ = ["app"]
