"""Generic mounter for in-process FastMCP servers (papers / sql / memory).

Every PaperHub-owned FastMCP server is an ASGI sub-app on the main FastAPI
app, fronted by PaperhubPapersRequestContextMiddleware (which opens a fresh
aiosqlite.Connection + Tracer per call from the X-Paperhub-* headers) and
sharing the parent's resolved Settings. Starlette does not propagate a
mounted sub-app's lifespan, so we chain it into the parent's.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from paperhub.mcp.server import PaperhubPapersRequestContextMiddleware

__all__ = ["mount_inprocess_mcp"]


def mount_inprocess_mcp(app: FastAPI, server: FastMCP, *, path: str) -> None:
    sub_app = server.streamable_http_app()
    sub_app.add_middleware(PaperhubPapersRequestContextMiddleware)
    app.mount(path, sub_app)

    parent_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _chained(target_app: FastAPI) -> AsyncIterator[None]:
        async with parent_lifespan(target_app), sub_app.router.lifespan_context(sub_app):
            sub_app.state.settings = target_app.state.settings
            yield

    app.router.lifespan_context = _chained
