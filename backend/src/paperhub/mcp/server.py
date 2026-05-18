"""In-process `paperhub-papers` FastMCP server (SRS v2.6, Plan C Task v2.5-3).

Re-exposes the three Research Agent dispatchers
(:func:`~paperhub.agents.research_tools.search_library_dispatch`,
:func:`~paperhub.agents.research_tools.search_semantic_scholar_dispatch`,
:func:`~paperhub.agents.research_tools.find_related_papers_dispatch`)
over the MCP wire protocol. Mounted as an ASGI sub-app on the existing
FastAPI app at ``/mcp`` — no extra process, no second port. External MCP
clients (Claude Desktop, Cursor) and the backend's own Research Agent
(post Task v2.5-4) reach the same URL.

**Same code path.** Each tool handler delegates to the exact same
``*_dispatch`` function the in-process agent calls today; this module only
adds the MCP surface. Tracer-step rows are written through the live
:class:`Tracer` carried on the per-call
:class:`~paperhub.mcp.server_context.PaperhubPapersRequestContext`, with
the canonical naming ``paper_search:papers.<tool>`` (the namespace prefix
comes from the FastMCP server name ``papers``).

**Per-call context plumbing.** The chat endpoint owns the run-level
``Tracer`` + ``Connection``; we don't tunnel those into FastMCP. Instead,
the Starlette middleware on the mounted sub-app opens a *fresh*
``aiosqlite.Connection`` and creates a ``Tracer`` keyed on
``X-Paperhub-Session-Id`` / ``X-Paperhub-Run-Id`` headers (so an external
Claude Desktop client gets working tracer rows from day one). Tests
bypass the middleware by calling
:func:`~paperhub.mcp.server_context.set_request_context` directly.

**Schema parity with TOOL_SCHEMAS.** FastMCP normally derives input
schemas from Python type hints; we post-register override each tool's
``parameters`` field with the exact JSON-schema dict already in
:data:`paperhub.agents.research_tools.TOOL_SCHEMAS` so the MCP-visible
contract matches the in-process LiteLLM palette one for one. Task v2.5-4
will remove the in-process palette entirely; until then this guarantees
the two surfaces stay in lockstep.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from paperhub.agents.research_tools import (
    TOOL_SCHEMAS,
    find_related_papers_dispatch,
    search_library_dispatch,
    search_semantic_scholar_dispatch,
)
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.mcp.server_context import (
    PaperhubPapersRequestContext,
    current_request_context,
    reset_request_context,
    set_request_context,
)
from paperhub.tracing.tracer import Tracer

__all__ = [
    "PaperhubPapersRequestContextMiddleware",
    "build_paperhub_papers_server",
    "mount_paperhub_papers_on",
]

_LOG = logging.getLogger(__name__)

# The FastMCP server name. Becomes the namespace prefix the agent (and any
# external MCP client) uses to address these tools: `papers.search_library`,
# etc. Tracer steps are tagged `paper_search:papers.<tool>` to match.
SERVER_NAME = "papers"


# ---------------------------------------------------------------------------
# Tool handlers — thin shims over the existing dispatchers
# ---------------------------------------------------------------------------
#
# Each handler:
#   1. resolves the per-call context (Tracer + Connection + session_id)
#      from the contextvar set by the middleware (production) or fixture
#      (tests). Missing context raises a clean error;
#   2. opens a tracer step named `paper_search:papers.<tool>`;
#   3. delegates to the existing dispatcher — zero behavioural change at
#      the SQL / HTTP / Chroma layer;
#   4. returns the structured dataclass list as plain dicts so FastMCP can
#      serialize them as `structuredContent`.
# ---------------------------------------------------------------------------


async def _search_library_handler(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    ctx = _require_context()
    async with ctx.tracer.step(
        agent="research",
        tool=f"paper_search:{SERVER_NAME}.search_library",
        model=None,
    ) as step:
        step.record_args({"query": query, "max_results": max_results})
        hits = [
            asdict(h)
            for h in await search_library_dispatch(
                query=query,
                max_results=max_results,
                conn=ctx.conn,
                session_id=ctx.session_id,
            )
        ]
        step.record_result({"count": len(hits)})
    return hits


async def _search_semantic_scholar_handler(
    query: str, max_results: int = 8,
) -> list[dict[str, Any]]:
    ctx = _require_context()
    async with ctx.tracer.step(
        agent="research",
        tool=f"paper_search:{SERVER_NAME}.search_semantic_scholar",
        model=None,
    ) as step:
        step.record_args({"query": query, "max_results": max_results})
        hits = [
            asdict(h)
            for h in await search_semantic_scholar_dispatch(
                query=query, max_results=max_results,
            )
        ]
        step.record_result({"count": len(hits)})
    return hits


async def _find_related_papers_handler(
    paper_id: str, mode: str, max_results: int = 8,
) -> list[dict[str, Any]]:
    ctx = _require_context()
    async with ctx.tracer.step(
        agent="research",
        tool=f"paper_search:{SERVER_NAME}.find_related_papers",
        model=None,
    ) as step:
        step.record_args(
            {"paper_id": paper_id, "mode": mode, "max_results": max_results},
        )
        # ``mode`` is a Literal["cites", "cited_by", "similar"] at the dispatcher
        # boundary; FastMCP hands us a plain string. The dispatcher validates.
        related = await find_related_papers_dispatch(
            paper_id=paper_id, mode=mode, max_results=max_results,  # type: ignore[arg-type]
        )
        step.record_result({"count": len(related)})
    return related


def _require_context() -> PaperhubPapersRequestContext:
    """Fetch the current request context or raise a clean error.

    Translates :class:`LookupError` from the unset ContextVar into a
    :class:`RuntimeError` whose message identifies the missing piece — so an
    external MCP client misconfiguring its request gets a useful diagnostic
    rather than an opaque transport error.
    """
    try:
        return current_request_context()
    except LookupError as exc:
        raise RuntimeError(
            "paperhub-papers MCP tool invoked without a request context "
            "(no X-Paperhub-Session-Id header set, and no test fixture "
            "primed the contextvar)"
        ) from exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_paperhub_papers_server() -> FastMCP:
    """Construct a FastMCP server exposing the three Research Agent tools.

    Tool input-schemas are taken verbatim from
    :data:`~paperhub.agents.research_tools.TOOL_SCHEMAS` so the MCP-visible
    contract matches the existing LiteLLM palette exactly. The descriptions
    likewise mirror the in-process palette — they were authored as the
    LLM-visible prompt for these tools and there is no separate MCP-visible
    description.

    The server's ``streamable_http_path`` is set to ``/`` so mounting at
    ``/mcp`` (via :func:`mount_paperhub_papers_on`) makes ``POST /mcp``
    the streamable-HTTP transport endpoint — matching the convention
    every other MCP server entry in ``mcp_servers.toml`` uses.
    """
    server = FastMCP(SERVER_NAME, streamable_http_path="/")
    schemas_by_name: dict[str, dict[str, Any]] = {
        s["function"]["name"]: s["function"] for s in TOOL_SCHEMAS
    }

    _register_tool(server, "search_library", _search_library_handler, schemas_by_name)
    _register_tool(
        server, "search_semantic_scholar",
        _search_semantic_scholar_handler, schemas_by_name,
    )
    _register_tool(
        server, "find_related_papers",
        _find_related_papers_handler, schemas_by_name,
    )
    return server


def _register_tool(
    server: FastMCP,
    name: str,
    handler: Callable[..., Awaitable[list[dict[str, Any]]]],
    schemas_by_name: dict[str, dict[str, Any]],
) -> None:
    """Register a handler with FastMCP and pin its JSON-schema to TOOL_SCHEMAS.

    FastMCP auto-derives input schemas from Python type hints, which would
    drift from :data:`TOOL_SCHEMAS` (default values, descriptions, the
    ``required`` set). We register the function for the runtime call path,
    then mutate the stored :class:`Tool`'s ``parameters`` field so
    ``tools/list`` advertises the exact schema the LiteLLM palette uses.
    """
    spec = schemas_by_name[name]
    server.add_tool(handler, name=name, description=spec["description"])
    tool = server._tool_manager.get_tool(name)  # noqa: SLF001 — see module docstring
    if tool is None:  # pragma: no cover — defensive
        raise RuntimeError(f"FastMCP failed to register tool {name!r}")
    tool.parameters = spec["parameters"]


# ---------------------------------------------------------------------------
# Mount on FastAPI
# ---------------------------------------------------------------------------


class PaperhubPapersRequestContextMiddleware(BaseHTTPMiddleware):
    """Populate the per-call :class:`PaperhubPapersRequestContext` from
    request headers, run the FastMCP handler, then tear the context down.

    Header contract:
      * ``X-Paperhub-Session-Id`` (required) — the chat session to dispatch
        against. Missing → the tool handler raises a clean error via
        :func:`_require_context`.
      * ``X-Paperhub-Run-Id`` (optional) — when present, tracer-step rows
        are attached to this run. When absent, the middleware creates a
        fresh ``runs`` row so an external Claude Desktop / Cursor caller
        gets a stand-alone trace.

    The middleware opens a private :class:`aiosqlite.Connection` per
    request (the chat endpoint owns its own connection on the parent
    request scope and we deliberately don't reach for it — keeps the
    loopback path identical to the external-client path).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        session_header = request.headers.get("x-paperhub-session-id")
        if session_header is None:
            # Pass through without a context — handler will raise a clean
            # error when it tries to access the contextvar. We deliberately
            # don't short-circuit here so other endpoints mounted under the
            # same path (none today, but defensively) aren't affected.
            return await call_next(request)
        try:
            session_id = int(session_header)
        except ValueError:
            return Response(
                content=f"X-Paperhub-Session-Id must be int, got {session_header!r}",
                status_code=400,
            )

        run_header = request.headers.get("x-paperhub-run-id")
        settings = load_settings()
        async with open_db(settings.db_path) as conn:
            if run_header is not None:
                try:
                    run_id = int(run_header)
                except ValueError:
                    return Response(
                        content=(
                            f"X-Paperhub-Run-Id must be int, got {run_header!r}"
                        ),
                        status_code=400,
                    )
            else:
                run_id = await _create_mcp_run(conn, session_id)

            tracer = Tracer(conn, run_id=run_id, branch="")
            ctx = PaperhubPapersRequestContext(
                conn=conn, session_id=session_id, run_id=run_id, tracer=tracer,
            )
            token = set_request_context(ctx)
            try:
                return await call_next(request)
            finally:
                reset_request_context(token)


async def _create_mcp_run(conn: aiosqlite.Connection, session_id: int) -> int:
    """Insert a fresh runs row for an external MCP caller.

    Used when the caller doesn't supply ``X-Paperhub-Run-Id`` — Claude
    Desktop / Cursor won't, and we still want tracer rows to land somewhere
    queryable via ``paperhub-replay``.
    """
    await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'running')",
        (session_id,),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


def mount_paperhub_papers_on(
    app: FastAPI, server: FastMCP, *, path: str = "/mcp",
) -> None:
    """Mount the FastMCP streamable-HTTP sub-app under ``path`` on the
    parent FastAPI app, with the request-context middleware attached.

    Starlette does NOT propagate a mounted sub-app's lifespan into the
    parent — we have to enter the FastMCP ``StreamableHTTPSessionManager``
    task group ourselves. We wrap the existing parent lifespan so the
    session manager is started during FastAPI startup and stopped during
    shutdown; without this, the first ``POST /mcp`` raises
    ``RuntimeError: Task group is not initialized``.

    Idempotent in spirit but not in fact — calling twice would mount the
    sub-app twice. The caller (FastAPI ``create_app``) is responsible for
    calling this exactly once during app construction.
    """
    sub_app = server.streamable_http_app()
    sub_app.add_middleware(PaperhubPapersRequestContextMiddleware)
    app.mount(path, sub_app)

    # Chain the sub-app's lifespan into the parent's.
    parent_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _chained(target_app: FastAPI) -> AsyncIterator[None]:
        async with parent_lifespan(target_app), sub_app.router.lifespan_context(sub_app):
            yield

    app.router.lifespan_context = _chained
    _LOG.info(
        "mcp.server mounted name=%s path=%s db=%s",
        server.name,
        path,
        _safe_db_path_log(),
    )


def _safe_db_path_log() -> str:
    """Return a stringified DB path for logging, with $HOME/cwd stripped."""
    try:
        db_path = load_settings().db_path
    except Exception:  # noqa: BLE001
        return "?"
    try:
        return str(db_path.relative_to(Path(os.getcwd())))
    except ValueError:
        return str(db_path)
