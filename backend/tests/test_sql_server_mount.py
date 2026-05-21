"""Wire-mount smoke test for the `sql` FastMCP server at ``/mcp-sql``.

Mirrors the boot pattern used by ``tests/mcp/test_server.py``'s
``test_mount_serves_mcp_endpoint`` (synchronous ``TestClient`` + ``create_app()``)
— the simplest pattern that proves the route is mounted and responds (not 404).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_sql_mcp_mounted_and_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke: ``/mcp-sql`` must be mounted and respond (not 404).

    Mirrors ``test_mount_serves_mcp_endpoint`` in ``tests/mcp/test_server.py``:
    boot the full app via ``TestClient``, POST to ``/mcp-sql/`` with a minimal
    body, and assert the route exists (status != 404).
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "1")
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")

    from paperhub.app import create_app

    app = create_app()
    with TestClient(app) as client:
        # POST to /mcp-sql without a proper MCP initialize body — we expect a
        # protocol-level error (400/405/406) rather than a 404 routing miss.
        resp = client.post(
            "/mcp-sql",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code != 404, (
        f"expected /mcp-sql to be mounted, got 404 — body: {resp.text!r}"
    )
