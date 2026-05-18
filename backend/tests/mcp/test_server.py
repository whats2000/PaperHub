"""Tests for `paperhub.mcp.server` — the in-process `paperhub-papers` FastMCP
surface that re-exposes the existing Research Agent dispatchers over the MCP
wire protocol (SRS v2.6, Plan C Task v2.5-3).

Coverage:
  * `tools/list` advertises exactly the three tools, with names + JSON-schemas
    matching `paperhub.agents.research_tools.TOOL_SCHEMAS`.
  * `tools/call` delegates to the existing dispatcher functions — observable
    via DB rows (for ``search_library``), respx mocks (for the two SS tools),
    and tracer-step ``tool_calls`` rows written through the threaded Tracer.
  * Tracer-step rows are tagged ``paper_search:papers.<tool>`` so the existing
    ToolStrip UI picks them up without changes.
  * Calling a tool without a request context returns a clean MCP error rather
    than crashing the server.
  * Mounting on FastAPI at ``/mcp`` exposes the streamable-HTTP transport on
    the backend's own port.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
import pytest_asyncio
import respx
from fastapi import FastAPI
from starlette.testclient import TestClient

from paperhub.agents.research_tools import _BASE_PAPER_TOOL_SCHEMAS
from paperhub.mcp.server import (
    PaperhubPapersRequestContextMiddleware,
    build_paperhub_papers_server,
    mount_paperhub_papers_on,
)
from paperhub.mcp.server_context import (
    PaperhubPapersRequestContext,
    current_request_context,
    set_request_context,
)
from paperhub.pipelines.semantic_scholar import API_BASE
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_and_run(
    migrated_db: aiosqlite.Connection,
) -> tuple[int, int]:
    """Insert a chat_sessions row + a runs row, return both ids."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    async with migrated_db.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])
    await migrated_db.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'running')",
        (session_id,),
    )
    await migrated_db.commit()
    async with migrated_db.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return session_id, int(row[0])


@pytest_asyncio.fixture
async def request_context(
    migrated_db: aiosqlite.Connection,
    session_and_run: tuple[int, int],
) -> PaperhubPapersRequestContext:
    session_id, run_id = session_and_run
    tracer = Tracer(migrated_db, run_id=run_id, branch="")
    return PaperhubPapersRequestContext(
        conn=migrated_db,
        session_id=session_id,
        run_id=run_id,
        tracer=tracer,
    )


async def _seed_paper_content(
    conn: aiosqlite.Connection,
    *,
    arxiv_id: str,
    title: str,
    abstract: str,
) -> int:
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:{arxiv_id}", "arxiv", arxiv_id, title, "[]", 2024, abstract,
            "/tmp/x.tex", "/tmp", "/tmp/x.html",
        ),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# Factory + schema parity
# ---------------------------------------------------------------------------


async def test_factory_returns_fastmcp_named_papers() -> None:
    """The MCP server name must be 'papers' — agent + external clients
    namespace tools by this name."""
    server = build_paperhub_papers_server()
    assert server.name == "papers"


async def test_tools_list_advertises_three_tools_matching_schemas() -> None:
    """The MCP server's tools/list output matches TOOL_SCHEMAS for the three
    Research Agent dispatchers (names + JSON-schemas)."""
    server = build_paperhub_papers_server()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "search_library",
        "search_semantic_scholar",
        "find_related_papers",
    }
    by_name = {t.name: t for t in tools}
    schemas_by_name = {
        s["function"]["name"]: s["function"] for s in _BASE_PAPER_TOOL_SCHEMAS
    }
    for name, tool in by_name.items():
        expected = schemas_by_name[name]
        assert tool.description == expected["description"], (
            f"description drift for {name!r}: tool={tool.description!r} "
            f"schema={expected['description']!r}"
        )
        assert tool.inputSchema == expected["parameters"], (
            f"input-schema drift for {name!r}: tool={tool.inputSchema} "
            f"schema={expected['parameters']}"
        )


# ---------------------------------------------------------------------------
# tools/call — delegates to the existing dispatchers
# ---------------------------------------------------------------------------


async def test_search_library_dispatches_with_context(
    migrated_db: aiosqlite.Connection,
    request_context: PaperhubPapersRequestContext,
) -> None:
    """A direct ``call_tool`` for ``search_library`` returns the same rows
    the dispatcher would, scoped to the request-context session."""
    pcid = await _seed_paper_content(
        migrated_db,
        arxiv_id="2401.00001",
        title="Attention Is All You Need",
        abstract="self-attention mechanism",
    )
    server = build_paperhub_papers_server()
    token = set_request_context(request_context)
    try:
        _content, structured = await server.call_tool(
            "search_library",
            {"query": "attention", "max_results": 5},
        )
    finally:
        # Clean up the contextvar regardless of test outcome.
        from paperhub.mcp.server_context import reset_request_context
        reset_request_context(token)

    assert "result" in structured
    hits = structured["result"]
    assert isinstance(hits, list)
    assert any(int(h["paper_content_id"]) == pcid for h in hits)
    assert hits[0]["title"] == "Attention Is All You Need"


async def test_search_library_writes_tracer_step(
    migrated_db: aiosqlite.Connection,
    request_context: PaperhubPapersRequestContext,
    session_and_run: tuple[int, int],
) -> None:
    """Every MCP tool call writes a ``tool_calls`` row with the
    ``paper_search:papers.<tool>`` naming convention."""
    _session_id, run_id = session_and_run
    await _seed_paper_content(
        migrated_db,
        arxiv_id="2401.00002",
        title="Some Title",
        abstract="abstract body",
    )

    server = build_paperhub_papers_server()
    from paperhub.mcp.server_context import reset_request_context
    token = set_request_context(request_context)
    try:
        await server.call_tool(
            "search_library", {"query": "some", "max_results": 3},
        )
    finally:
        reset_request_context(token)

    async with migrated_db.execute(
        "SELECT agent, tool, status FROM tool_calls WHERE run_id = ?",
        (run_id,),
    ) as cur:
        rows = await cur.fetchall()
    assert any(
        r[0] == "research"
        and r[1] == "paper_search:papers.search_library"
        and r[2] == "ok"
        for r in rows
    ), f"expected tracer step paper_search:papers.search_library, got {rows!r}"


@respx.mock
async def test_search_semantic_scholar_dispatches_with_http_mock(
    migrated_db: aiosqlite.Connection,
    request_context: PaperhubPapersRequestContext,
) -> None:
    """The MCP tool round-trips through ``search_semantic_scholar_dispatch``;
    HTTP is mocked via respx so the test is hermetic."""
    respx.get(f"{API_BASE}/paper/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "paperId": "abcd1234",
                        "title": "Mamba",
                        "abstract": "linear-time state space",
                        "year": 2024,
                        "authors": [{"name": "Albert"}],
                        "externalIds": {"ArXiv": "2312.00001"},
                        "openAccessPdf": None,
                    },
                ],
            },
        ),
    )

    server = build_paperhub_papers_server()
    from paperhub.mcp.server_context import reset_request_context
    token = set_request_context(request_context)
    try:
        _content, structured = await server.call_tool(
            "search_semantic_scholar",
            {"query": "mamba state space", "max_results": 5},
        )
    finally:
        reset_request_context(token)

    hits = structured["result"]
    assert isinstance(hits, list)
    assert hits[0]["paper_id"] == "arxiv:2312.00001"
    assert hits[0]["title"] == "Mamba"
    assert hits[0]["arxiv_id"] == "2312.00001"


@respx.mock
async def test_find_related_papers_dispatches(
    migrated_db: aiosqlite.Connection,
    request_context: PaperhubPapersRequestContext,
) -> None:
    """The MCP tool round-trips through ``find_related_papers_dispatch``."""
    respx.get(f"{API_BASE}/paper/arXiv:2402.99999/citations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "citingPaper": {
                            "title": "Follow-up",
                            "abstract": "abs",
                            "year": 2025,
                            "authors": [{"name": "Z"}],
                            "externalIds": {"ArXiv": "2503.00001"},
                        },
                    },
                ],
            },
        ),
    )
    server = build_paperhub_papers_server()
    from paperhub.mcp.server_context import reset_request_context
    token = set_request_context(request_context)
    try:
        _content, structured = await server.call_tool(
            "find_related_papers",
            {"paper_id": "arxiv:2402.99999", "mode": "cited_by", "max_results": 5},
        )
    finally:
        reset_request_context(token)

    related = structured["result"]
    assert isinstance(related, list)
    assert related[0]["arxiv_id"] == "2503.00001"
    assert related[0]["title"] == "Follow-up"


async def test_call_without_request_context_returns_clean_error() -> None:
    """A tool call with no PaperhubPapersRequestContext set must return a
    structured error (mediated by FastMCP) rather than crashing."""
    server = build_paperhub_papers_server()
    # ContextVar is empty by default — no set_request_context.
    with pytest.raises(Exception) as exc_info:
        await server.call_tool(
            "search_library", {"query": "x", "max_results": 3},
        )
    # The message should mention the missing context, not be a bare
    # AttributeError on None.
    assert "context" in str(exc_info.value).lower(), exc_info.value


# ---------------------------------------------------------------------------
# server_context helpers
# ---------------------------------------------------------------------------


async def test_current_request_context_raises_when_unset() -> None:
    """``current_request_context`` raises a typed error when no context is set
    (rather than returning a None that crashes the handler downstream)."""
    with pytest.raises(LookupError):
        current_request_context()


async def test_set_request_context_roundtrip(
    request_context: PaperhubPapersRequestContext,
) -> None:
    from paperhub.mcp.server_context import reset_request_context
    token = set_request_context(request_context)
    try:
        got = current_request_context()
        assert got is request_context
        assert got.session_id == request_context.session_id
        assert got.run_id == request_context.run_id
    finally:
        reset_request_context(token)
    # After reset, the context is gone again.
    with pytest.raises(LookupError):
        current_request_context()


# ---------------------------------------------------------------------------
# Mounting on FastAPI
# ---------------------------------------------------------------------------


async def test_mount_attaches_streamable_http_at_path() -> None:
    """Mounting the FastMCP sub-app on FastAPI at ``/mcp`` makes the streamable
    HTTP transport reachable at that path."""
    app = FastAPI()
    server = build_paperhub_papers_server()
    mount_paperhub_papers_on(app, server, path="/mcp")
    # Starlette `/mcp` route must exist after mount.
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/mcp" in paths, paths


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_mount_serves_mcp_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke test the ASGI mount: hit the mounted path. We don't speak the
    full MCP wire protocol here (the in-memory ClientSession tests cover
    that). We just assert the mount is wired and the path responds rather
    than 404s."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))

    from paperhub.app import create_app
    app = create_app()
    with TestClient(app) as client:
        # POST to /mcp without a proper MCP initialize body — we expect a
        # protocol-level error (400/405/406) rather than a 404 routing miss.
        resp = client.post(
            "/mcp",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code != 404, (
        f"expected /mcp to be mounted, got 404 — body: {resp.text!r}"
    )


# ---------------------------------------------------------------------------
# In-memory wire-protocol round-trip via ClientSession
# ---------------------------------------------------------------------------


async def test_inmemory_clientsession_lists_three_tools(
    request_context: PaperhubPapersRequestContext,
) -> None:
    """Drive the server with an in-memory MCP ClientSession (bypasses the
    HTTP transport but exercises the MCP wire protocol). Asserts that
    ``tools/list`` returns the three Research Agent tools."""
    from mcp.shared.memory import create_connected_server_and_client_session

    server = build_paperhub_papers_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()
        names = {t.name for t in result.tools}
        assert names == {
            "search_library",
            "search_semantic_scholar",
            "find_related_papers",
        }


async def test_inmemory_clientsession_calls_search_library(
    migrated_db: aiosqlite.Connection,
    request_context: PaperhubPapersRequestContext,
) -> None:
    """Drive a ``tools/call`` via the in-memory ClientSession with a context
    primed by the test fixture. The dispatcher must return the seeded paper."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from paperhub.mcp.server_context import reset_request_context

    pcid = await _seed_paper_content(
        migrated_db,
        arxiv_id="2401.55555",
        title="Wire Protocol Paper",
        abstract="testing the wire",
    )

    server = build_paperhub_papers_server()
    token = set_request_context(request_context)
    try:
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "search_library",
                arguments={"query": "wire protocol", "max_results": 5},
            )
    finally:
        reset_request_context(token)

    assert not result.isError
    structured: Any = result.structuredContent
    assert structured is not None and "result" in structured
    hits = structured["result"]
    assert any(int(h["paper_content_id"]) == pcid for h in hits)


# ---------------------------------------------------------------------------
# End-to-end ASGI: middleware -> contextvar -> tool handler -> tracer row
# ---------------------------------------------------------------------------
#
# The in-memory ClientSession tests above bypass ASGI middleware entirely.
# This block exercises the production code path that matters most for an
# external Claude Desktop / Cursor call:
#
#   real HTTP POST
#     -> PaperhubPapersRequestContextMiddleware.dispatch
#         -> opens aiosqlite.Connection
#         -> creates Tracer + PaperhubPapersRequestContext
#         -> sets the ContextVar
#         -> FastMCP routes to the tool handler
#         -> handler reads ContextVar via current_request_context()
#         -> handler writes a tool_calls row via the tracer
#
# `BaseHTTPMiddleware` historically has contextvar-propagation pitfalls
# (different task scope for `call_next`); the assertion that a `tool_calls`
# row was written under the same `run_id` the middleware created proves
# the contextvar threaded through correctly.
#
# We use FastMCP's `json_response=True, stateless_http=True` mode here so
# the wire protocol is a single POST -> single JSON response (no SSE, no
# session-id round-tripping). The middleware under test is identical
# either way — what we're validating is its contextvar plumbing.
# ---------------------------------------------------------------------------


async def test_real_http_middleware_threads_context_into_tool_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a real HTTP POST to ``/mcp`` flows through the middleware,
    the contextvar reaches the tool handler, the handler returns the seeded
    paper, and a ``tool_calls`` row is written under the middleware-created
    run id. Closes the gap left by the in-memory ClientSession tests above,
    which never exercise ASGI middleware."""
    from httpx import ASGITransport, AsyncClient

    from paperhub.app import _lifespan
    from paperhub.config import load_settings
    from paperhub.db.migrate import apply_schema

    # Stand up a workspace + DB the lifespan can target.
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))
    settings = load_settings()

    # Seed schema + a paper_content row + a chat session before the app
    # starts. apply_schema is idempotent, so the lifespan re-applying is fine.
    async with aiosqlite.connect(settings.db_path) as setup_conn:
        await setup_conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(setup_conn)
        await setup_conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await setup_conn.commit()
        async with setup_conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        session_id = int(row[0])
        await setup_conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
            "source_path, source_dir_path, html_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "arxiv:2401.99988", "arxiv", "2401.99988",
                "Middleware Integration Test",
                "[]", 2024, "verifying contextvar plumbing through ASGI",
                "/tmp/x.tex", "/tmp", "/tmp/x.html",
            ),
        )
        await setup_conn.commit()
        async with setup_conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        seeded_pcid = int(row[0])

    # Build a FastAPI app with a FastMCP server in stateless+json mode so
    # the wire protocol is a single POST -> JSON response. The middleware
    # under test is the same class used in production.
    server = build_paperhub_papers_server()
    server.settings.json_response = True
    server.settings.stateless_http = True

    app = FastAPI(lifespan=_lifespan)
    mount_paperhub_papers_on(app, server, path="/mcp")

    # Sanity-check that the production-style middleware is what's attached.
    sub_app = next(r.app for r in app.routes if getattr(r, "path", None) == "/mcp")
    middleware_classes = [m.cls for m in sub_app.user_middleware]
    assert PaperhubPapersRequestContextMiddleware in middleware_classes, (
        f"production middleware missing from mounted sub-app: {middleware_classes}"
    )

    # NB:
    #   * FastMCP enables DNS-rebinding protection by default; the Host header
    #     must match `127.0.0.1:*` / `localhost:*` / `[::1]:*` — hence the
    #     explicit port in base_url. Bare `127.0.0.1` (no port) is rejected.
    #   * ASGITransport doesn't run the FastAPI lifespan automatically;
    #     enter it manually so the FastMCP StreamableHTTPSessionManager
    #     task group is alive when we POST.
    async with (
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client,
        app.router.lifespan_context(app),
    ):
        # Send `initialize` first (required even in stateless mode for
        # the wire-protocol handshake). Accept both JSON and SSE per
        # the streamable-HTTP transport spec.
        init_resp = await client.post(
            "/mcp/",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-Paperhub-Session-Id": str(session_id),
            },
            content=json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }),
        )
        assert init_resp.status_code == 200, (
            f"initialize failed: {init_resp.status_code} {init_resp.text!r}"
        )

        # tools/call — the test's actual point.
        call_resp = await client.post(
            "/mcp/",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-Paperhub-Session-Id": str(session_id),
                "MCP-Protocol-Version": "2025-06-18",
            },
            content=json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_library",
                    "arguments": {
                        "query": "middleware integration",
                        "max_results": 5,
                    },
                },
            }),
        )
        assert call_resp.status_code == 200, (
            f"tools/call failed: {call_resp.status_code} {call_resp.text!r}"
        )
        payload = call_resp.json()

    # (a) The seeded paper must round-trip back through the FastMCP wire
    #     protocol, proving the tool handler ran with a live context.
    assert "result" in payload, payload
    structured = payload["result"].get("structuredContent")
    assert structured is not None, (
        f"missing structuredContent in tools/call result: {payload['result']!r}"
    )
    hits = structured["result"]
    assert any(int(h["paper_content_id"]) == seeded_pcid for h in hits), hits

    # (b) The middleware auto-created a runs row and the tracer wrote a
    #     tool_calls row under it — proves the contextvar threaded through
    #     BaseHTTPMiddleware -> FastMCP handler correctly, and Fix 3's
    #     status finalisation flipped the row from `running` -> `ok`.
    async with aiosqlite.connect(settings.db_path) as conn:
        async with conn.execute(
            "SELECT id, status FROM runs WHERE session_id = ?",
            (session_id,),
        ) as cur:
            run_rows = await cur.fetchall()
        assert run_rows, "middleware should have auto-created a runs row"
        # The mounted middleware creates exactly one run per HTTP request;
        # initialize is a notification-style request that does not enter
        # the contextvar branch (no session header parsing failure), so we
        # may see 1 or 2 runs depending on protocol flow. The tool_calls
        # row's run_id will pin which one we care about.
        run_ids = {int(r[0]) for r in run_rows}
        statuses = {int(r[0]): r[1] for r in run_rows}

        async with conn.execute(
            "SELECT run_id, agent, tool, status FROM tool_calls "
            "WHERE tool = 'paper_search:papers.search_library'",
        ) as cur:
            tc_rows = await cur.fetchall()
        assert tc_rows, (
            "expected a tool_calls row tagged paper_search:papers.search_library"
        )
        tc_run_ids = {int(r[0]) for r in tc_rows}
        assert tc_run_ids <= run_ids, (
            f"tool_calls run_id {tc_run_ids!r} not in middleware-created "
            f"runs {run_ids!r} — contextvar plumbing leaked"
        )
        # Fix 3: the auto-created run should be flipped to 'ok' on the way
        # out, not left at 'running'.
        for rid in tc_run_ids:
            assert statuses[rid] == "ok", (
                f"middleware-auto-created run {rid} ended at {statuses[rid]!r}, "
                f"expected 'ok' — Fix 3 regressed"
            )
        for _, _, _, status in tc_rows:
            assert status == "ok", tc_rows
