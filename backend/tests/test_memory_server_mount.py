"""Wire-mount smoke test for the `memory` FastMCP server at ``/mcp-memory``.

Mirrors ``test_sql_server_mount.py`` — the simplest pattern that proves the
route is mounted and responds (not 404). Also confirms that all three chained
in-process MCP servers (papers / sql / memory) are mounted simultaneously.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_memory_mcp_mounted_and_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke: ``/mcp-memory`` must be mounted and respond (not 404).

    POST to ``/mcp-memory/`` with a minimal body and assert the route exists
    (status != 404 — a protocol-level 400/405/406 is fine, 404 is not).
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "1")
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")

    from paperhub.app import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp-memory",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code != 404, (
        f"expected /mcp-memory to be mounted, got 404 — body: {resp.text!r}"
    )


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_all_three_mcp_servers_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke: papers (/mcp), sql (/mcp-sql), and memory (/mcp-memory) are all
    mounted in a single app — the three-way lifespan chain must not blow up.
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "1")
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")

    from paperhub.app import create_app

    app = create_app()
    with TestClient(app) as client:
        for path in ("/mcp", "/mcp-sql", "/mcp-memory"):
            resp = client.post(
                path,
                content=b"{}",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code != 404, (
                f"expected {path} to be mounted, got 404 — body: {resp.text!r}"
            )
