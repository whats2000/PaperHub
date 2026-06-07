import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
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
from paperhub.api import decks as decks_api
from paperhub.api import memories as memories_api
from paperhub.api import papers as papers_api
from paperhub.api import sessions as sessions_api
from paperhub.config import load_settings
from paperhub.db.connection import configure_connection, open_db
from paperhub.db.migrate import (
    apply_schema,
    purge_deleted_sessions,
    sweep_orphan_session_folders,
)
from paperhub.mcp import (
    MCPRegistry,
    build_paperhub_memory_server,
    build_paperhub_papers_server,
    build_paperhub_sql_server,
    mount_inprocess_mcp,
    mount_paperhub_papers_on,
)
from paperhub.mcp.config import ensure_config_seeded, resolve_config_path
from paperhub.pipelines.marker_worker import build_worker_pipeline
from paperhub.pipelines.marker_worker import run_worker as run_marker_worker

_LOG = logging.getLogger("paperhub.app")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Transient connection drops to upstream LLM providers (Gemini/Vertex
    # mid-stream disconnects, 5xx, etc.) are an expected operating mode.
    # litellm.num_retries makes EVERY litellm.acompletion / completion call
    # retry on those errors before raising — across brief, plan, render,
    # revise, notes, edit, paper_qa, chitchat. Permanent errors (bad
    # request, auth) still propagate immediately.
    import litellm
    litellm.num_retries = 3

    settings = load_settings()
    app.state.settings = settings
    async with open_db(settings.db_path) as conn:
        await apply_schema(conn)
        # Reclaim storage from chats soft-deleted longer ago than the
        # retention window (their messages/runs/papers cascade away, and
        # their workspace/chat_session/<id>/ folder is rmtree'd).
        purged = await purge_deleted_sessions(
            conn,
            settings.session_retention_days,
            workspace_dir=settings.workspace_dir,
        )
        if purged:
            _LOG.info("purged %d soft-deleted session(s) past retention", purged)
        # Sweep orphan folders (no matching chat_sessions row — partial-write
        # crashes, pre-cascade leaks). Runs regardless of whether anything was
        # purged this boot.
        orphans = await sweep_orphan_session_folders(conn, settings.workspace_dir)
        if orphans:
            _LOG.info("swept %d orphan session folder(s)", orphans)

    # MCP registry: load mcp_servers.toml + construct (NOT connect) clients.
    # Connection is lazy on first tool use so this never blocks startup —
    # critical for loopback servers (e.g. the future `papers` MCP) that
    # listen on the backend's own port and aren't accepting connections yet.
    mcp_toml = resolve_config_path()
    ensure_config_seeded(mcp_toml)
    app.state.mcp_registry = MCPRegistry()
    await app.state.mcp_registry.startup(mcp_toml)

    # Background Marker upgrade worker (Plan F2.1): drains PDF papers marked
    # 'marker_pending' by re-extracting them via Marker (one at a time — a
    # concurrent Marker call OOMs a small GPU), upgrading the on-disk
    # PaperAsset, and re-chunking. Durable: the queue lives in
    # the DB, so pending papers resume across restarts. Runs on a DEDICATED
    # long-lived connection (the migration conn above is closed). Disabled with
    # PAPERHUB_MARKER_WORKER=0 (tests set this so they never spawn it).
    app.state.marker_worker_task = None
    app.state.marker_worker_stop = None
    app.state.marker_worker_conn = None
    if os.environ.get("PAPERHUB_MARKER_WORKER", "1") != "0":
        worker_conn = await aiosqlite.connect(settings.db_path)
        await configure_connection(worker_conn)
        worker_pipeline = build_worker_pipeline(worker_conn, settings)
        stop = asyncio.Event()
        app.state.marker_worker_conn = worker_conn
        app.state.marker_worker_stop = stop
        app.state.marker_worker_task = asyncio.create_task(
            run_marker_worker(
                worker_pipeline, worker_conn,
                stop=stop, max_pages=settings.marker_max_pages,
            ),
            name="paperhub-marker-worker",
        )
        _LOG.info("marker worker started")

    # The dense-vector RAG stack (embedder + reranker + Chroma) was removed, so
    # there is no model pre-warm to wait on. Defer the "ready" banner to a
    # background task so it lands AFTER uvicorn logs "Application startup
    # complete" — the banner is the human-facing "we're up" marker and should be
    # the last thing on screen. uvicorn emits that log the instant this lifespan
    # startup phase yields, so an inline print here would always precede it.
    app.state.ready_banner_task = asyncio.create_task(
        _announce_ready(settings, app), name="paperhub-ready-banner",
    )

    try:
        yield
    finally:
        # Cancel the ready banner if boot was so fast it hasn't fired yet.
        ready_task = getattr(app.state, "ready_banner_task", None)
        if ready_task is not None and not ready_task.done():
            ready_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await ready_task
        # Stop the Marker worker (guard with a timeout so a slow in-flight
        # Marker call can't hang shutdown indefinitely).
        worker_stop = app.state.marker_worker_stop
        worker_task = app.state.marker_worker_task
        if worker_stop is not None and worker_task is not None:
            worker_stop.set()
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(worker_task, timeout=30.0)
            if not worker_task.done():
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker_task
        worker_conn = app.state.marker_worker_conn
        if worker_conn is not None:
            with contextlib.suppress(Exception):
                await worker_conn.close()
        await app.state.mcp_registry.shutdown()


# PaperHub wordmark (figlet "slant"). A clear, iconic "we're up" marker so the
# transient connection errors the UI logs while polling a not-yet-listening
# backend aren't mistaken for a failed boot — printed once the whole stack
# (DB, MCP) is wired.
_BANNER_ART = [
    r"    ____                        __  __      __  ",
    r"   / __ \____ _____  ___  _____/ / / /_  __/ /_ ",
    r"  / /_/ / __ `/ __ \/ _ \/ ___/ /_/ / / / / __ \ ",
    r" / ____/ /_/ / /_/ /  __/ /  / __  / /_/ / /_/ / ",
    r"/_/    \__,_/ .___/\___/_/  /_/ /_/\__,_/_.___/  ",
    r"           /_/                                   ",
]


async def _announce_ready(settings: object, app: FastAPI) -> None:
    """Print the boot banner once uvicorn has finished startup.

    uvicorn logs ``Application startup complete`` the instant the lifespan
    startup phase yields. Scheduling this as a background task (rather than an
    inline print before the yield) lets the banner land AFTER that line, so the
    'we're up' marker is the last thing on screen. The short sleep cedes the
    loop so the startup-complete log wins the race; cancelled cleanly at
    shutdown if boot was too fast for it to fire."""
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(0.2)
        _print_boot_banner(settings, app)


def _print_boot_banner(_settings: object, app: FastAPI) -> None:
    """Print an iconic 'boot complete' banner to stdout once the full stack is
    wired. Skipped under tests / when ``PAPERHUB_BOOT_BANNER=0``."""
    if os.environ.get("PAPERHUB_BOOT_BANNER", "1") == "0":
        return

    import sys

    # A real console renders ANSI colour + Unicode box chars (Windows writes via
    # the UTF-16 console API, so code page doesn't matter). When redirected to a
    # pipe/file the encoding may be a legacy code page that mangles — or can't
    # encode — box chars, so fall back to plain ASCII there.
    fancy = sys.stdout.isatty()

    def c(code: str) -> str:
        return code if fancy else ""

    amber, dim, bold, green, reset = (
        c("\033[38;5;208m"),
        c("\033[2m"),
        c("\033[1m"),
        c("\033[38;5;42m"),
        c("\033[0m"),
    )

    try:
        from importlib.metadata import version

        ver = f"v{version('paperhub')}"
    except Exception:  # noqa: BLE001
        ver = ""

    mcp_names = sorted(getattr(app.state.mcp_registry, "_clients", {}) or {})
    mcp = ", ".join(mcp_names) if mcp_names else "none (web search optional)"

    dash = "—" if fancy else "-"
    # Body lines (plain text; width is measured on these, color added after).
    rows = [
        f"{'PaperHub ready':<16}{ver}",
        "",
        f"{'Open the app':<16}http://localhost:5173",
        f"{'MCP servers':<16}{mcp}",
        "",
        "Connection errors logged above were the UI polling before",
        f"this point {dash} boot is complete, they're safe to ignore.",
    ]
    width = max(len(r) for r in rows)
    tl, tr, bl, br, h, v = ("╭", "╮", "╰", "╯", "─", "│") if fancy else (
        "+", "+", "+", "+", "-", "|",
    )
    top = f"{amber}{tl}{h * (width + 2)}{tr}{reset}"
    bottom = f"{amber}{bl}{h * (width + 2)}{br}{reset}"

    out = ["", *[f"{amber}{line}{reset}" for line in _BANNER_ART], "", top]
    for r in rows:
        # Bold the headline, dim the footnote, plain for the status rows.
        if r.startswith("PaperHub ready"):
            text = f"{bold}{green}{r}{reset}"
        elif r.startswith("Connection") or r.startswith("this point"):
            text = f"{dim}{r}{reset}"
        else:
            text = r
        pad = " " * (width - len(r))
        out.append(f"{amber}{v}{reset} {text}{pad} {amber}{v}{reset}")
    out.append(bottom)
    out.append("")
    # The banner is cosmetic — never let an encoding hiccup break boot.
    with contextlib.suppress(Exception):
        print("\n".join(out), flush=True)




def create_app() -> FastAPI:
    app = FastAPI(title="PaperHub", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:4173"],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # X-Paperhub-Session-Id is sent by the Memory Manager PATCH/DELETE
        # (FR-11) for ownership checks; without it in allow_headers the browser
        # CORS preflight is rejected and the request fails before reaching us.
        allow_headers=["Content-Type", "X-Paperhub-Session-Id"],
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(sessions_api.router)
    app.include_router(papers_api.router)
    app.include_router(chunks_api.router)
    app.include_router(memories_api.router)
    app.include_router(decks_api.router)
    # Mount the in-process `paperhub-papers` FastMCP server at /mcp.
    # External MCP clients (Claude Desktop, Cursor) and the agent (post
    # Task v2.5-4) reach the three Research Agent tools over the MCP wire
    # protocol via this URL — uniform dispatch path with `web.*`.
    mount_paperhub_papers_on(app, build_paperhub_papers_server(), path="/mcp")
    # Mount the in-process `paperhub-sql` FastMCP server at /mcp-sql.
    # The SQL Agent (Plan E) reaches the three read-only SQL tools over the
    # MCP wire protocol via this URL — same loopback convention as papers.
    mount_inprocess_mcp(app, build_paperhub_sql_server(), path="/mcp-sql")
    # Mount the in-process `paperhub-memory` FastMCP server at /mcp-memory.
    # The ONLY write MCP surface: recall/add/edit/forget with scope enforcement.
    # The Memory Agent (Plan E) reaches all four tools over the MCP wire
    # protocol via this URL — same loopback convention as papers + sql.
    mount_inprocess_mcp(app, build_paperhub_memory_server(), path="/mcp-memory")
    return app


app = create_app()
