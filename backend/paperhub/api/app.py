"""FastAPI ASGI app factory.

The app is built via `create_app()` so tests can swap settings via env
before construction. The startup hook applies pending SQLite migrations
once; runtime endpoints assume the schema is up-to-date.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from paperhub.api.schemas import HealthResponse
from paperhub.config import get_settings
from paperhub.data.db import apply_migrations, connect


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        apply_migrations(settings.db_path)
        yield

    app = FastAPI(title="PaperHub", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        with connect(settings.db_path) as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return HealthResponse(status="ok", app="paperhub", schema_version=row[0])

    return app
