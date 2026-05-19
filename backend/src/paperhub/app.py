import asyncio
import logging
import os
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

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
from paperhub.modelserver.spawn import ensure_running as _modelserver_ensure_running
from paperhub.modelserver.spawn import terminate_subprocess as _modelserver_terminate
from paperhub.rag.chroma import ChromaStore

_LOG = logging.getLogger("paperhub.app")


def _mcp_servers_toml_path() -> Path:
    """Resolve `mcp_servers.toml`. Env override → backend repo sibling."""
    env = os.environ.get("PAPERHUB_MCP_CONFIG")
    if env:
        return Path(env)
    # backend/src/paperhub/app.py → backend/mcp_servers.toml
    return Path(__file__).resolve().parents[2] / "mcp_servers.toml"


def _ensure_mcp_servers_toml(path: Path) -> None:
    """Seed `mcp_servers.toml` from `mcp_servers.toml.example` on first boot.

    The `papers` server is REQUIRED for the agent (no in-process fallback
    post Task v2.5-4), so a fresh clone with no `mcp_servers.toml` would
    silently boot with an empty tool palette and the LLM would hallucinate
    its way through paper_search. Auto-seeding from the checked-in example
    closes that gap — operators can still edit the file afterwards.

    Skips when the file already exists (operator-customised) or when the
    example is missing (env-overridden config path that doesn't follow
    the sibling-template convention).
    """
    if path.exists():
        return
    example = path.with_name(path.name + ".example")
    if not example.exists():
        _LOG.info(
            "paperhub.app mcp_servers.toml absent + no example at %s; "
            "registry will start empty (agent paper_search will fail)",
            example,
        )
        return
    shutil.copyfile(example, path)
    _LOG.info(
        "paperhub.app seeded %s from %s (first-boot default)",
        path.name, example.name,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.settings = settings
    async with open_db(settings.db_path) as conn:
        await apply_schema(conn)
    # ChromaStore holds a PersistentClient; chromadb manages its own cleanup.
    app.state.chroma = ChromaStore(settings.chroma_dir)

    # Spawn the model server (sentence-transformers + cross-encoder) as
    # a sibling process so uvicorn --reload on backend code can't reset
    # the ~110 MB embedder + ~80 MB reranker weights. Skipped when the
    # operator forced in-process models (PAPERHUB_INPROCESS_MODELS=1) or
    # when the server is already reachable (e.g. operator started it
    # manually, or a previous backend run's subprocess outlived its
    # parent). Best-effort: a failed spawn doesn't block boot — the
    # HTTP-client embedder will surface a connection error to the
    # caller, and the operator can switch to inprocess_models.
    app.state.modelserver_proc = None
    if not settings.inprocess_models:
        app.state.modelserver_proc = await _modelserver_ensure_running(
            host=settings.model_server_host,
            port=settings.model_server_port,
        )

    # MCP registry: load mcp_servers.toml + construct (NOT connect) clients.
    # Connection is lazy on first tool use so this never blocks startup —
    # critical for loopback servers (e.g. the future `papers` MCP) that
    # listen on the backend's own port and aren't accepting connections yet.
    mcp_toml = _mcp_servers_toml_path()
    _ensure_mcp_servers_toml(mcp_toml)
    app.state.mcp_registry = MCPRegistry()
    await app.state.mcp_registry.startup(mcp_toml)

    # Pre-warm embedder and reranker singletons so the first real paper_qa
    # request doesn't pay the cold-cache tax. With the model server
    # running out-of-process, this warms its model cache (not the
    # worker's) — so the warm state survives backend reloads.
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
        _modelserver_terminate(app.state.modelserver_proc)


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
