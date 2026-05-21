import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# On Windows, asyncio's SelectorEventLoop doesn't support subprocess
# spawn (NotImplementedError from `create_subprocess_exec`). The MCP
# registry needs to spawn `npx open-websearch` on first boot, so force
# the Proactor loop policy BEFORE uvicorn binds an event loop. No-op
# on non-Windows (the policy doesn't exist there).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from paperhub.api import chat, health
from paperhub.api import chunks as chunks_api
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
from paperhub.mcp.config import ensure_config_seeded, resolve_config_path
from paperhub.modelserver.spawn import ensure_running as _modelserver_ensure_running
from paperhub.rag.chroma import ChromaStore

_LOG = logging.getLogger("paperhub.app")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.settings = settings
    async with open_db(settings.db_path) as conn:
        await apply_schema(conn)
    # ChromaStore holds a PersistentClient; chromadb manages its own cleanup.
    app.state.chroma = ChromaStore(settings.chroma_dir)

    # Model server: detach-and-leak. If an instance is already
    # reachable on host:port, reuse it — this is what makes uvicorn
    # --reload zero-cost: the previous worker's spawn outlives the
    # reload, the new worker probes /health, sees green, skips spawn.
    # If nothing is listening, spawn ONE detached subprocess (Windows
    # CREATE_NEW_PROCESS_GROUP / Unix start_new_session) so a future
    # worker restart won't take it down with us. We intentionally do
    # NOT track this proc on app.state and do NOT terminate it at
    # shutdown — that's what was killing the modelserver on every
    # reload before. Operators who want explicit lifecycle use
    # `scripts/start.ps1`; otherwise the modelserver leaks across
    # backend restarts (cleaned up at OS reboot, or by manual
    # taskkill / pkill paperhub-modelserver). Skipped entirely when
    # PAPERHUB_INPROCESS_MODELS=1.
    if not settings.inprocess_models:
        await _modelserver_ensure_running(
            host=settings.model_server_host,
            port=settings.model_server_port,
        )

    # MCP registry: load mcp_servers.toml + construct (NOT connect) clients.
    # Connection is lazy on first tool use so this never blocks startup —
    # critical for loopback servers (e.g. the future `papers` MCP) that
    # listen on the backend's own port and aren't accepting connections yet.
    mcp_toml = resolve_config_path()
    ensure_config_seeded(mcp_toml)
    app.state.mcp_registry = MCPRegistry()
    await app.state.mcp_registry.startup(mcp_toml)

    # Pre-warm embedder + reranker as a FIRE-AND-FORGET background task
    # so lifespan finishes immediately. The modelserver's first /embed
    # call triggers SentenceTransformer load (HF Hub download on cold
    # cache — minutes on a slow network); blocking lifespan on that
    # would hang the backend for the full download. Real ingest
    # requests arriving before warm-up completes will simply queue
    # behind the in-flight model load on the modelserver side.
    # Guarded by PAPERHUB_PREWARM_MODELS=0 so offline / CI envs can skip.
    app.state.prewarm_task = None
    if os.environ.get("PAPERHUB_PREWARM_MODELS", "1") != "0":
        app.state.prewarm_task = asyncio.create_task(
            _prewarm_models(), name="paperhub-prewarm",
        )

    try:
        yield
    finally:
        # Cancel pre-warm if still in flight at shutdown.
        prewarm = app.state.prewarm_task
        if prewarm is not None and not prewarm.done():
            prewarm.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await prewarm
        # Lifespan: chroma cleanup handled internally by chromadb.
        await app.state.mcp_registry.shutdown()


async def _prewarm_models() -> None:
    """Background warm-up of the modelserver's embedder + reranker.

    Runs the blocking HTTP calls in a worker thread (via
    ``asyncio.to_thread``) so the event loop stays responsive to
    incoming requests during warm-up. Best-effort: any failure
    (modelserver not running, slow HF download, network blip, etc.)
    is logged at WARN and swallowed — the first real request will
    just pay the load cost itself.
    """
    try:
        from paperhub.pipelines.embedder import get_embedder
        from paperhub.rag.reranker import get_reranker

        _LOG.info("paperhub.app prewarm starting (background)")
        await asyncio.to_thread(get_embedder().embed, [""])
        await asyncio.to_thread(
            get_reranker().rerank, "warm", ["up"], 1,
        )
        _LOG.info("paperhub.app prewarm complete")
    except asyncio.CancelledError:
        _LOG.info("paperhub.app prewarm cancelled at shutdown")
        raise
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "paperhub.app prewarm failed (%s: %s) — first ingest "
            "will pay the model-load cost. Is `scripts/start.ps1` "
            "running, or PAPERHUB_INPROCESS_MODELS=1 set?",
            type(exc).__name__, exc,
        )


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
    app.include_router(chunks_api.router)
    # Mount the in-process `paperhub-papers` FastMCP server at /mcp.
    # External MCP clients (Claude Desktop, Cursor) and the agent (post
    # Task v2.5-4) reach the three Research Agent tools over the MCP wire
    # protocol via this URL — uniform dispatch path with `web.*`.
    mount_paperhub_papers_on(app, build_paperhub_papers_server(), path="/mcp")
    return app


app = create_app()
