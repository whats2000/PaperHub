"""FastAPI ASGI app factory.

The app is built via `create_app()` so tests can swap settings via env
before construction. The startup hook applies pending SQLite migrations
once; runtime endpoints assume the schema is up-to-date.

Lifespan-managed resources (Phase A integration fixes):
- D3: ArXiv MCP subprocess session (LaunchedMcpSessions) — avoids anyio
      cancel-scope mismatch from per-request context-manager lifecycle.
- D6: Embedder + ChromaVectorStore + Retriever — cached on app.state so the
      first request does NOT trigger a ~80 MB HuggingFace model download.
      Initialization happens lazily on the first ``get_retriever`` call and
      the result is stored on ``app.state.retriever`` for subsequent requests.
      This avoids any model-load crash during TestClient lifespan startup.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from paperhub.api.routes.chat import router as chat_router
from paperhub.api.routes.papers import router as papers_router
from paperhub.api.schemas import HealthResponse
from paperhub.config import get_settings
from paperhub.data.db import apply_migrations, connect

# Module-level import so tests can patch ``paperhub.api.app.LaunchedMcpSessions``
# to prevent subprocess spawn during TestClient lifespan.
from paperhub.mcp.launchers import LaunchedMcpSessions

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 1. Apply DB migrations
        apply_migrations(settings.db_path)

        # 2. Mark retriever as not yet initialized (D6: will be created lazily
        #    on first get_retriever call and cached on app.state)
        _app.state.retriever = None

        # 3. D3: Start arXiv MCP subprocess session (lifespan-owned to avoid
        #    anyio cancel-scope task mismatch on GC finalization).
        #    The pre-launched dispatcher is stored on app.state.mcp_dispatcher
        #    so routes can reuse the session without spawning new subprocesses.
        #
        #    ``LaunchedMcpSessions.__aenter__`` never raises — it logs a warning
        #    and sets self._started=False if the subprocess can't be launched.
        #    ``make_dispatcher()`` returns None when not started so routes fall
        #    back to per-request lazy connect (make_dispatcher() function).
        #
        #    Tests can patch ``paperhub.api.app.LaunchedMcpSessions`` to prevent
        #    subprocess spawn during TestClient lifespan.
        async with LaunchedMcpSessions(settings) as mcp_sessions:
            # None when subprocess didn't start; routes detect and fall back
            _app.state.mcp_dispatcher = mcp_sessions.make_dispatcher()
            yield

    app = FastAPI(title="PaperHub", lifespan=lifespan)

    app.include_router(chat_router)
    app.include_router(papers_router)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        with connect(settings.db_path) as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return HealthResponse(status="ok", app="paperhub", schema_version=row[0])

    return app
