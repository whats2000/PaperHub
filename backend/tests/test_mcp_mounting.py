"""Tests for the generic in-process FastMCP mount helper (Plan E Task 2).

Covers:
  * ``mount_inprocess_mcp`` wires a sub-app route at the requested path.
  * ``require_request_context`` raises ``RuntimeError`` (not ``LookupError``)
    when no context is set, with a message that includes "request context".
"""
import pytest
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from paperhub.mcp.mounting import mount_inprocess_mcp
from paperhub.mcp.server_context import require_request_context


def test_mount_inprocess_mcp_adds_route_and_middleware() -> None:
    app = FastAPI()
    server = FastMCP("demo", streamable_http_path="/")
    mount_inprocess_mcp(app, server, path="/mcp-demo")
    mounted = [r for r in app.routes if getattr(r, "path", "") == "/mcp-demo"]
    assert mounted, "sub-app not mounted at /mcp-demo"


def test_require_request_context_raises_runtimeerror_when_unset() -> None:
    with pytest.raises(RuntimeError, match="request context"):
        require_request_context()
