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
from paperhub.config import Settings, load_settings
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema, purge_deleted_sessions
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
        # Reclaim storage from chats soft-deleted longer ago than the
        # retention window (their messages/runs/papers cascade away).
        purged = await purge_deleted_sessions(conn, settings.session_retention_days)
        if purged:
            _LOG.info("purged %d soft-deleted session(s) past retention", purged)
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
        # Defer the "ready" banner until warm-up resolves — it sometimes
        # finishes last, and announcing ready while the models are still cold
        # is misleading. The task prints the banner when it completes.
        app.state.prewarm_task = asyncio.create_task(
            _prewarm_models(settings, app), name="paperhub-prewarm",
        )
    else:
        # No warm-up to wait on — the stack is ready right now.
        _print_boot_banner(settings, app)

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


# PaperHub wordmark (figlet "slant"). A clear, iconic "we're up" marker so the
# transient connection errors the UI logs while polling a not-yet-listening
# backend aren't mistaken for a failed boot — printed once the whole stack
# (DB, vectors, model server, MCP) is wired AND model warm-up has resolved,
# since warm-up can finish last.
_BANNER_ART = [
    r"    ____                        __  __      __  ",
    r"   / __ \____ _____  ___  _____/ / / /_  __/ /_ ",
    r"  / /_/ / __ `/ __ \/ _ \/ ___/ /_/ / / / / __ \ ",
    r" / ____/ /_/ / /_/ /  __/ /  / __  / /_/ / /_/ / ",
    r"/_/    \__,_/ .___/\___/_/  /_/ /_/\__,_/_.___/  ",
    r"           /_/                                   ",
]


def _print_boot_banner(settings: object, app: FastAPI) -> None:
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

    s = settings  # has model_server_*, inprocess_models, db_path
    model = (
        "in-process"
        if getattr(s, "inprocess_models", False)
        else f"{getattr(s, 'model_server_host', '?')}:{getattr(s, 'model_server_port', '?')}"
    )
    mcp_names = sorted(getattr(app.state.mcp_registry, "_clients", {}) or {})
    mcp = ", ".join(mcp_names) if mcp_names else "none (web search optional)"

    dash = "—" if fancy else "-"
    # Body lines (plain text; width is measured on these, color added after).
    rows = [
        f"{'PaperHub ready':<16}{ver}",
        "",
        f"{'Open the app':<16}http://localhost:5173",
        f"{'Model server':<16}{model}",
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


async def _prewarm_models(settings: Settings, app: FastAPI) -> None:
    """Background warm-up of the modelserver's embedder + reranker.

    Runs the blocking HTTP calls in a worker thread (via
    ``asyncio.to_thread``) so the event loop stays responsive to
    incoming requests during warm-up. Best-effort: any failure
    (modelserver not running, slow HF download, network blip, etc.)
    is logged at WARN and swallowed — the first real request will
    just pay the load cost itself.

    Prints the "ready" boot banner once warm-up resolves (success OR
    non-cancel failure) — this is the genuinely-ready moment, since warm-up
    can finish after the rest of the stack. Skipped if cancelled at shutdown.
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
        raise  # shutting down — no banner
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "paperhub.app prewarm failed (%s: %s) — first ingest "
            "will pay the model-load cost. Is `scripts/start.ps1` "
            "running, or PAPERHUB_INPROCESS_MODELS=1 set?",
            type(exc).__name__, exc,
        )
    # API has been serving since lifespan yielded; models are now warm (or will
    # load lazily). Announce ready.
    _print_boot_banner(settings, app)


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
