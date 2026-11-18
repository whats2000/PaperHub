"""Lifespan integration: MCP registry is attached and does not deadlock on
loopback servers (the v2.6 lazy-connect invariant).

Drives the FastAPI lifespan directly (httpx + ASGITransport does NOT run
lifespan), so we can assert on `app.state` cleanly. The loopback test
exercises a TOML pointing at the backend's own port: eager connect would
block forever during lifespan startup because nothing is listening yet.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paperhub.app import create_app
from paperhub.mcp.registry import MCPRegistry


async def test_lifespan_attaches_registry_when_no_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))

    app = create_app()
    # `app.router.lifespan_context` runs the @asynccontextmanager-wrapped
    # lifespan we defined; httpx ASGITransport does not trigger it.
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.mcp_registry, MCPRegistry)
        schemas = await app.state.mcp_registry.aggregate_tool_schemas()
        assert schemas == []


async def test_lifespan_does_not_block_on_loopback_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v2.6 lazy-connect invariant: a loopback server in mcp_servers.toml
    must not deadlock lifespan startup (uvicorn isn't accepting yet)."""
    toml = tmp_path / "mcp_servers.toml"
    toml.write_text(
        """
[[server]]
name = "papers"
transport = "streamable_http"
url = "http://127.0.0.1:1/mcp"
expose = ["search_library"]
timeout_seconds = 1.0
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(toml))

    # Record every connect: the invariant is that startup makes none, not
    # that it is fast (a wall-clock bound flaked under load).
    from paperhub.mcp import client as mcp_client
    connects: list[str] = []
    real_connect = mcp_client.MCPClient.connect

    async def _recording_connect(self: mcp_client.MCPClient) -> None:
        connects.append(getattr(self, "name", "?"))
        await real_connect(self)
    monkeypatch.setattr(mcp_client.MCPClient, "connect", _recording_connect)

    app = create_app()

    async def _enter_and_exit_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.mcp_registry, MCPRegistry)
            assert connects == [], "lifespan startup connected to an MCP server"

    # Lazy connect means startup never touches the dead loopback. The
    # wait_for is a hard cap against a real hang.
    await asyncio.wait_for(_enter_and_exit_lifespan(), timeout=5.0)
    assert connects == []


async def test_lifespan_with_missing_toml_path_is_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "nope.toml"))

    app = create_app()
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.mcp_registry, MCPRegistry)
        assert await app.state.mcp_registry.aggregate_tool_schemas() == []
