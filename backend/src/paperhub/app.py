import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from paperhub.api import chat, health
from paperhub.api import papers as papers_api
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema
from paperhub.rag.chroma import ChromaStore


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.settings = settings
    async with open_db(settings.db_path) as conn:
        await apply_schema(conn)
    # ChromaStore holds a PersistentClient; chromadb manages its own cleanup.
    app.state.chroma = ChromaStore(settings.chroma_dir)

    # Pre-warm embedder and reranker singletons so the first real paper_qa
    # request doesn't pay the ~5s model-load cost (Plan C field-test #3).
    # These are lazy singletons; touching them here loads the underlying
    # SentenceTransformer / CrossEncoder weights from the HF cache.
    # Guarded by PAPERHUB_PREWARM_MODELS=0 so offline / CI envs can skip.
    if os.environ.get("PAPERHUB_PREWARM_MODELS", "1") != "0":
        try:
            from paperhub.pipelines.embedder import get_embedder
            from paperhub.rag.reranker import get_reranker

            get_embedder().embed([""])
            get_reranker().rerank("warm", ["up"], top_k=1)
        except Exception:  # noqa: BLE001
            # Pre-warm is best-effort — never block boot.
            pass

    yield
    # Lifespan: chroma cleanup handled internally by chromadb.


def create_app() -> FastAPI:
    app = FastAPI(title="PaperHub", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(papers_api.router)
    return app


app = create_app()
