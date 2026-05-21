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

**Canonical schemas.** FastMCP normally derives input schemas from
Python type hints; we post-register override each tool's ``parameters``
field with the exact JSON-schema dict in
:data:`paperhub.agents.research_tools._BASE_PAPER_TOOL_SCHEMAS` so this
module is the single source of truth for the ``papers.*`` JSON contract.
The agent reads its palette from the MCP registry (Task v2.5-4) — there
is no other in-process palette to keep in sync.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from paperhub.agents.research_tools import (
    _BASE_PAPER_TOOL_SCHEMAS,
    find_related_papers_dispatch,
    search_library_dispatch,
    search_semantic_scholar_dispatch,
)
from paperhub.config import Settings, load_settings
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


def _tool_step(
    ctx: PaperhubPapersRequestContext, tool: str, args: dict[str, Any],
) -> AbstractAsyncContextManager[Any]:
    """Build a tracer-step context manager scoped to this MCP call.

    On the loopback path (``caller_supplied_run=True``), the calling agent's
    outer ``tracer.step`` already records this tool call under the same
    ``run_id``; writing a second row here would violate the UNIQUE
    constraint on ``(run_id, branch, step_index)``. Return a ``nullcontext``
    so the handler can ``async with`` it uniformly and the dispatch body
    stays branch-free.

    On the external-client path (Claude Desktop / Cursor — the middleware
    auto-created the run), this wrap is the only source of ``tool_calls``
    rows; we record args + result the same way the agent's outer wrap does
    in the loopback case.
    """
    if ctx.caller_supplied_run:
        return nullcontext()
    step_cm = ctx.tracer.step(agent="research", tool=tool, model=None)

    @asynccontextmanager
    async def _record(args: dict[str, Any]) -> AsyncIterator[Any]:
        async with step_cm as step:
            step.record_args(args)
            yield step

    return _record(args)


async def _search_library_handler(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    ctx = _require_context()
    args = {"query": query, "max_results": max_results}
    async with _tool_step(ctx, f"paper_search:{SERVER_NAME}.search_library", args) as step:
        hits = [
            asdict(h)
            for h in await search_library_dispatch(
                query=query,
                max_results=max_results,
                conn=ctx.conn,
                session_id=ctx.session_id,
            )
        ]
        if step is not None:
            step.record_result({"count": len(hits)})
    return hits


async def _search_semantic_scholar_handler(
    query: str, max_results: int = 8,
) -> list[dict[str, Any]]:
    ctx = _require_context()
    args = {"query": query, "max_results": max_results}
    async with _tool_step(
        ctx, f"paper_search:{SERVER_NAME}.search_semantic_scholar", args,
    ) as step:
        hits = [
            asdict(h)
            for h in await search_semantic_scholar_dispatch(
                query=query, max_results=max_results,
            )
        ]
        if step is not None:
            step.record_result({"count": len(hits)})
    return hits


async def _find_related_papers_handler(
    paper_id: str, mode: str, max_results: int = 8,
) -> list[dict[str, Any]]:
    ctx = _require_context()
    args = {"paper_id": paper_id, "mode": mode, "max_results": max_results}
    async with _tool_step(
        ctx, f"paper_search:{SERVER_NAME}.find_related_papers", args,
    ) as step:
        # ``mode`` is a Literal["cites", "cited_by", "similar"] at the dispatcher
        # boundary; FastMCP hands us a plain string. The dispatcher validates.
        related = await find_related_papers_dispatch(
            paper_id=paper_id, mode=mode, max_results=max_results,  # type: ignore[arg-type]
        )
        if step is not None:
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
    :data:`~paperhub.agents.research_tools._BASE_PAPER_TOOL_SCHEMAS` —
    this module owns the canonical ``papers.*`` JSON contract advertised
    to MCP clients (the agent itself, Claude Desktop, Cursor).

    The server's ``streamable_http_path`` is set to ``/`` so mounting at
    ``/mcp`` (via :func:`mount_paperhub_papers_on`) makes ``POST /mcp``
    the streamable-HTTP transport endpoint — matching the convention
    every other MCP server entry in ``mcp_servers.toml`` uses.
    """
    server = FastMCP(SERVER_NAME, streamable_http_path="/")
    server.settings.json_response = True
    server.settings.stateless_http = True
    schemas_by_name: dict[str, dict[str, Any]] = {
        s["function"]["name"]: s["function"] for s in _BASE_PAPER_TOOL_SCHEMAS
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
    """Register a handler with FastMCP and pin its JSON-schema to _BASE_PAPER_TOOL_SCHEMAS.

    FastMCP auto-derives input schemas from Python type hints, which would
    drift from :data:`_BASE_PAPER_TOOL_SCHEMAS` (default values, descriptions, the
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

    Settings are read once from ``request.app.state.settings`` (populated
    during FastAPI lifespan startup) rather than re-loaded per request —
    re-running :func:`load_settings` on every MCP call would re-read ~10
    env vars and run a filesystem ``mkdir`` syscall on the hot path.
    """

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
                media_type="text/plain",
            )

        run_header = request.headers.get("x-paperhub-run-id")
        # Settings are populated on the parent FastAPI app in the lifespan
        # (`paperhub.app._lifespan`) and copied onto the mounted sub-app's
        # state by the chained lifespan in `mount_paperhub_papers_on` —
        # `request.app` here is the sub-app (Starlette overwrites
        # `scope["app"]` on mount dispatch), so the copy is the only way
        # to surface the parent's resolved Settings without re-running
        # `load_settings()` on the hot path. If settings is None we want
        # a hard failure — it indicates a programming error.
        settings = getattr(request.app.state, "settings", None)
        if settings is None:  # pragma: no cover — defensive
            raise RuntimeError(
                "PaperhubPapersRequestContextMiddleware requires "
                "app.state.settings to be populated during lifespan startup"
            )
        assert isinstance(settings, Settings)
        async with open_db(settings.db_path) as conn:
            caller_supplied_run = run_header is not None
            if caller_supplied_run:
                try:
                    run_id = int(run_header)  # type: ignore[arg-type]
                except ValueError:
                    return Response(
                        content=(
                            f"X-Paperhub-Run-Id must be int, got {run_header!r}"
                        ),
                        status_code=400,
                        media_type="text/plain",
                    )
            else:
                run_id = await _create_mcp_run(conn, session_id)

            tracer = Tracer(conn, run_id=run_id, branch="")
            ctx = PaperhubPapersRequestContext(
                conn=conn,
                session_id=session_id,
                run_id=run_id,
                tracer=tracer,
                caller_supplied_run=caller_supplied_run,
            )
            token = set_request_context(ctx)
            try:
                response = await call_next(request)
            except BaseException:
                # Auto-created runs: mark error on exception so the row
                # doesn't sit at `running` forever. Caller-supplied runs
                # keep their lifecycle owned by the parent context.
                if not caller_supplied_run:
                    await _finalise_mcp_run(conn, run_id, status="error")
                raise
            finally:
                reset_request_context(token)
            # Auto-created runs: mark ok on successful exit. Without this,
            # every external Claude Desktop / Cursor call would leave a
            # permanent `running` row.
            if not caller_supplied_run:
                await _finalise_mcp_run(conn, run_id, status="ok")
            return response


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


async def _finalise_mcp_run(
    conn: aiosqlite.Connection, run_id: int, *, status: str,
) -> None:
    """Mark an auto-created MCP runs row as finished.

    Mirrors the ``UPDATE runs SET finished_at = datetime('now'), status = ?``
    shape used by :func:`paperhub.api.chat._finalise` so a replay walking the
    runs table sees a consistent shape regardless of which surface ran the
    request. Only invoked when the middleware itself auto-created the run;
    caller-supplied run ids keep their lifecycle owned by the parent.
    """
    await conn.execute(
        "UPDATE runs SET finished_at = datetime('now'), status = ? WHERE id = ?",
        (status, run_id),
    )
    await conn.commit()


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

    Delegates the generic mount mechanics to
    :func:`paperhub.mcp.mounting.mount_inprocess_mcp` — the local import
    avoids a circular import (mounting.py imports
    ``PaperhubPapersRequestContextMiddleware`` from this module).
    """
    from paperhub.mcp.mounting import mount_inprocess_mcp
    mount_inprocess_mcp(app, server, path=path)
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
