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

from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
import pytest_asyncio
import respx
from fastapi import FastAPI
from starlette.testclient import TestClient

from paperhub.agents.research_tools import TOOL_SCHEMAS
from paperhub.mcp.server import build_paperhub_papers_server, mount_paperhub_papers_on
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
        s["function"]["name"]: s["function"] for s in TOOL_SCHEMAS
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
