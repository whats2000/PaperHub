"""MCP client layer (SRS v2.5, §III-6.1).

Per-server connectors over the official Anthropic ``mcp`` Python SDK.
Plan C v2.5 ships ``open-webSearch`` as the first consumer; Plan E
(sqlite MCP) and Plan G (filesystem + ``paperhub.*`` MCP) reuse this
layer unchanged by adding ``[[server]]`` blocks to ``mcp_servers.toml``.

`MCPRegistry` is provided by `paperhub.mcp.registry` (Task v2.5-2) and
re-exported here once that module lands.
"""
from __future__ import annotations

from .client import MCPClient
from .client_context import (
    ClientHeadersContext,
    current_client_headers_context,
    reset_client_headers_context,
    set_client_headers_context,
)
from .config import MCPServerConfig, load_mcp_servers
from .errors import MCPError, MCPToolError, MCPUnavailableError
from .mounting import mount_inprocess_mcp
from .registry import MCPRegistry
from .server import build_paperhub_papers_server, mount_paperhub_papers_on
from .server_context import (
    PaperhubPapersRequestContext,
    current_request_context,
    require_request_context,
    reset_request_context,
    set_request_context,
)
from .sql_server import build_paperhub_sql_server

__all__ = [
    "ClientHeadersContext",
    "MCPClient",
    "MCPError",
    "MCPRegistry",
    "MCPServerConfig",
    "MCPToolError",
    "MCPUnavailableError",
    "PaperhubPapersRequestContext",
    "build_paperhub_papers_server",
    "build_paperhub_sql_server",
    "current_client_headers_context",
    "current_request_context",
    "load_mcp_servers",
    "mount_inprocess_mcp",
    "mount_paperhub_papers_on",
    "require_request_context",
    "reset_client_headers_context",
    "reset_request_context",
    "set_client_headers_context",
    "set_request_context",
]
