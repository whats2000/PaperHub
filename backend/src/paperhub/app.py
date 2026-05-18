import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from paperhub.api import chat, health
from paperhub.api import papers as papers_api
from paperhub.api import sessions as sessions_api
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema
from paperhub.mcp import (
    MCPRegistry,
    build_paperhub_papers_server,
    mount_paperhub_papers_on,
)
from paperhub.rag.chroma import ChromaStore


def _mcp_servers_toml_path() -> Path:
    """Resolve `mcp_servers.toml`. Env override → backend repo sibling."""
    env = os.environ.get("PAPERHUB_MCP_CONFIG")
    if env:
        return Path(env)
    # backend/src/paperhub/app.py → backend/mcp_servers.toml
    return Path(__file__).resolve().parents[2] / "mcp_servers.toml"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.settings = settings
    async with open_db(settings.db_path) as conn:
        await apply_schema(conn)
    # ChromaStore holds a PersistentClient; chromadb manages its own cleanup.
    app.state.chroma = ChromaStore(settings.chroma_dir)

    # MCP registry: load mcp_servers.toml + construct (NOT connect) clients.
    # Connection is lazy on first tool use so this never blocks startup —
    # critical for loopback servers (e.g. the future `papers` MCP) that
    # listen on the backend's own port and aren't accepting connections yet.
    app.state.mcp_registry = MCPRegistry()
    await app.state.mcp_registry.startup(_mcp_servers_toml_path())

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

    try:
        yield
    finally:
        # Lifespan: chroma cleanup handled internally by chromadb.
        await app.state.mcp_registry.shutdown()


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
    app.include_router(sessions_api.router)
    app.include_router(papers_api.router)
    # Mount the in-process `paperhub-papers` FastMCP server at /mcp.
    # External MCP clients (Claude Desktop, Cursor) and the agent (post
    # Task v2.5-4) reach the three Research Agent tools over the MCP wire
    # protocol via this URL — uniform dispatch path with `web.*`.
    mount_paperhub_papers_on(app, build_paperhub_papers_server(), path="/mcp")
    return app


app = create_app()
