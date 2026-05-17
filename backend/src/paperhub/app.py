from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from paperhub.api import health
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.settings = settings
    async with open_db(settings.db_path) as conn:
        await apply_schema(conn)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="PaperHub", lifespan=_lifespan)
    app.include_router(health.router)
    return app


app = create_app()
