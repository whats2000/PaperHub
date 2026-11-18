"""End-to-end loopback proof for Task v2.5-7.

This is the test that would have caught the v2.5-6 bug originally:
the agent's production code path is

    chat endpoint
      → set_client_headers_context(session_id=…, run_id=…)
        → LangGraph paper_search subgraph
          → MCPRegistry.call('papers.search_library', …)
            → MCPClient.call_tool
              → streamablehttp_client(url, headers={X-Paperhub-Session-Id: …})
                → HTTP POST /mcp
                  → PaperhubPapersRequestContextMiddleware (reads the header)
                    → FastMCP tool handler
                      → search_library_dispatch
                      → tracer writes a tool_calls row

before v2.5-7, `streamablehttp_client(url)` was called with no headers,
the middleware saw no `X-Paperhub-Session-Id` header, fell through to the
"pass through without a context" branch, and the FastMCP handler then
raised because the contextvar was unset — 500 to the agent, masking the
bug as "transient MCP failure".

The test mounts the FastMCP server on a FastAPI app and instantiates an
`MCPClient` pointing at the in-process loopback `/mcp`. It does NOT spin
up the full chat endpoint — that would pull in LiteLLM + tracing harness
which makes the test fragile. Instead it sets the client_headers
contextvar directly (the same shim the chat endpoint uses) and asserts
the call succeeds + lands in a `tool_calls` row tagged with the same
session_id we set.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
import uvicorn
from fastapi import FastAPI

from paperhub.app import _lifespan
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema
from paperhub.mcp.client import MCPClient
from paperhub.mcp.client_context import (
    ClientHeadersContext,
    reset_client_headers_context,
    set_client_headers_context,
)
from paperhub.mcp.config import MCPServerConfig
from paperhub.mcp.errors import MCPToolError, MCPUnavailableError
from paperhub.mcp.server import build_paperhub_papers_server, mount_paperhub_papers_on

pytestmark = pytest.mark.asyncio


async def _seed_paper_and_session(
    db_path: Path,
) -> tuple[int, int]:
    """Apply the schema, insert a chat session + a paper_content row.
    Returns ``(session_id, paper_content_id)``."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        session_id = int(row[0])
        await conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "arxiv:2407.55555", "arxiv", "2407.55555",
                "Loopback Proof Paper",
                "[]", 2024,
                "proving that the chat -> mcp loopback works end-to-end",
                "/tmp/source.tex", "/tmp", "/tmp/source.html",
            ),
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        return session_id, int(row[0])


async def _find_free_port() -> int:
    """Bind to port 0 and immediately release — return the OS-assigned port.

    We need a real listening port because `MCPClient` runs the streamable
    HTTP transport over a real httpx client; ASGITransport-in-process
    would bypass the very middleware we're testing.
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


async def test_chat_to_mcp_loopback_session_header_threaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full production path: chat endpoint sets the client_headers
    contextvar, `MCPClient.call_tool` forwards the session header to the
    loopback `/mcp`, the FastMCP middleware accepts the call (no 400),
    and a `tool_calls` row is written under the same session_id.

    This is the regression guard for Task v2.5-7: before this commit,
    `streamablehttp_client(url)` was called with no headers and the
    middleware rejected the request.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))
    settings = load_settings()

    session_id, seeded_pcid = await _seed_paper_and_session(settings.db_path)

    # Build a real FastAPI app with the FastMCP server mounted at /mcp,
    # serve it with uvicorn on a real port.
    server = build_paperhub_papers_server()
    # `json_response=True, stateless_http=True` makes the wire protocol a
    # single POST→JSON exchange — simpler for `MCPClient` to drive in a
    # short-lived test (no SSE state to manage).
    server.settings.json_response = True
    server.settings.stateless_http = True

    app = FastAPI(lifespan=_lifespan)
    mount_paperhub_papers_on(app, server, path="/mcp")

    port = await _find_free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
        # HTTP only: no websockets stack (its legacy module is deprecated).
        ws="none",
    )
    uv_server = uvicorn.Server(config)
    serve_task = asyncio.create_task(uv_server.serve())
    try:
        # Wait until uvicorn reports started — polling instead of a
        # blanket sleep so we don't add 1s to the suite when the server
        # comes up in 5ms.
        for _ in range(200):
            if uv_server.started:
                break
            await asyncio.sleep(0.025)
        assert uv_server.started, "uvicorn failed to start within ~5s"

        # Drive the production code path: set the contextvar, instantiate
        # a real MCPClient, call_tool — exactly what the chat endpoint
        # plus MCPRegistry would do.
        client = MCPClient(
            MCPServerConfig(
                name="papers",
                transport="streamable_http",
                url=f"http://127.0.0.1:{port}/mcp/",
                expose=["search_library"],
                aliases={},
                timeout_seconds=10.0,
            ),
        )

        token = set_client_headers_context(
            ClientHeadersContext(session_id=session_id, run_id=None),
        )
        try:
            await client.connect()
            result = await client.call_tool(
                "search_library",
                {"query": "loopback proof", "max_results": 5},
            )
        finally:
            reset_client_headers_context(token)
            await client.disconnect()
    finally:
        uv_server.should_exit = True
        await serve_task

    # (a) The call must have succeeded — `MCPClient` returns the
    # structuredContent payload. The dispatcher emits a list under
    # ``result`` (FastMCP convention for list-returning tools).
    assert isinstance(result, dict), result
    hits = result["result"]
    assert isinstance(hits, list)
    assert any(int(h["paper_content_id"]) == seeded_pcid for h in hits), hits

    # (b) The middleware saw the session header and auto-created a runs
    # row under THIS session_id (not some other). And the tracer wrote a
    # tool_calls row under that run, tagged paper_search:papers.search_library.
    async with aiosqlite.connect(settings.db_path) as conn:
        async with conn.execute(
            "SELECT id, status FROM runs WHERE session_id = ?",
            (session_id,),
        ) as cur:
            runs = await cur.fetchall()
        assert runs, (
            "middleware should have auto-created a runs row under the "
            "session_id we passed through the contextvar"
        )

        async with conn.execute(
            "SELECT run_id, agent, tool, status FROM tool_calls "
            "WHERE tool = 'paper_search:papers.search_library'",
        ) as cur:
            tc_rows = await cur.fetchall()
        assert tc_rows, (
            "expected a tool_calls row tagged paper_search:papers.search_library "
            "— if missing, the contextvar didn't reach the FastMCP handler"
        )
        run_ids = {int(r[0]) for r in runs}
        for tc_run_id, _, _, status in tc_rows:
            assert int(tc_run_id) in run_ids, (
                "tool_call row's run_id is not in this session's runs — "
                "the middleware created a run under a different session"
            )
            assert status == "ok", f"tool_call ended at status={status!r}"


async def test_loopback_call_without_contextvar_is_rejected_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flipside guard: without the contextvar set, `MCPClient` sends
    no `X-Paperhub-Session-Id` header — the FastMCP handler should
    surface a clear "missing context" error rather than silently
    succeeding under a stale / random session id.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))
    settings = load_settings()

    # Just need the schema in place.
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)

    server = build_paperhub_papers_server()
    server.settings.json_response = True
    server.settings.stateless_http = True

    app = FastAPI(lifespan=_lifespan)
    mount_paperhub_papers_on(app, server, path="/mcp")

    port = await _find_free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", lifespan="on", ws="none",
    )
    uv_server = uvicorn.Server(config)
    serve_task = asyncio.create_task(uv_server.serve())
    try:
        for _ in range(200):
            if uv_server.started:
                break
            await asyncio.sleep(0.025)
        assert uv_server.started

        client = MCPClient(
            MCPServerConfig(
                name="papers",
                transport="streamable_http",
                url=f"http://127.0.0.1:{port}/mcp/",
                expose=["search_library"],
                aliases={},
                timeout_seconds=10.0,
            ),
        )
        # NO set_client_headers_context here.
        await client.connect()
        try:
            with pytest.raises((MCPToolError, MCPUnavailableError)):
                await client.call_tool(
                    "search_library",
                    {"query": "x", "max_results": 3},
                )
        finally:
            await client.disconnect()
    finally:
        uv_server.should_exit = True
        await serve_task
