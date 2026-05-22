"""In-process read-only `sql` FastMCP server (SRS v2.16, Plan E Wave 1, wire-fix).

Tools (namespace ``sql.*``):
  * list_tables()        -> list[str]            (the §III-6 allowlist)
  * describe(table)       -> list[{name,type}]    (PRAGMA table_info, allowlisted)
  * query(sql)            -> {columns, rows}       (sqlglot-validated SELECT/WITH)

Rejections (non-allowlisted table / non-SELECT verb) are returned as
``{"error": "rejected", "reason": ...}`` rather than raised, so the calling
SQL Agent can mark its run-level tracer step status='rejected' (NFR-05).
The per-request server tracer must NOT write on the loopback path
(step_index collides with the agent's run-level tracer).
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from paperhub.mcp.server_context import require_request_context
from paperhub.mcp.sql_safety import ALLOWED_TABLES, SqlValidationError, validate_read_only_sql

__all__ = [
    "SQL_SERVER_NAME",
    "_describe_handler",
    "_list_tables_handler",
    "_query_handler",
    "build_paperhub_sql_server",
]

SQL_SERVER_NAME = "sql"
_MAX_ROWS = 200


async def _list_tables_handler() -> list[str]:
    """Return the sorted list of allowlisted tables the SQL agent may query."""
    return sorted(ALLOWED_TABLES)


async def _describe_handler(table: str) -> Any:
    """Return [{name, type}] column metadata for one allowlisted table.

    Rejects non-allowlisted table names with a structured error payload so the
    calling agent can mark its tracer step as rejected rather than raising.

    Note: ``table`` is interpolated into a PRAGMA statement. This is safe
    ONLY because we gate on ALLOWED_TABLES membership first — those are
    hardcoded identifiers, never raw user text.
    """
    if table.lower() not in ALLOWED_TABLES:
        return {"error": "rejected", "reason": f"table {table!r} is not allowlisted"}
    ctx = require_request_context()
    async with ctx.conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return [{"name": r[1], "type": r[2]} for r in rows]


async def _query_handler(sql: str) -> Any:
    """Execute one read-only SQL statement and return {columns, rows}.

    The statement is validated by :func:`~paperhub.mcp.sql_safety.validate_read_only_sql`
    before execution. Writes, DDL, PRAGMA, multi-statement SQL, and references to
    non-allowlisted tables are returned as ``{"error": "rejected", "reason": ...}``
    so the calling agent can record a status='rejected' tracer step.

    Results are capped at ``_MAX_ROWS`` rows (200) to keep response payloads
    manageable for the LLM context window.
    """
    try:
        validate_read_only_sql(sql)
    except SqlValidationError as exc:
        return {"error": "rejected", "reason": str(exc)}
    ctx = require_request_context()
    try:
        async with ctx.conn.execute(sql) as cur:
            fetched = await cur.fetchmany(_MAX_ROWS)
            columns = [d[0] for d in (cur.description or [])]
    except Exception as exc:  # aiosqlite wraps sqlite3 errors as sqlite3.Error subclasses
        # Return a structured error so the SQL Agent's self-repair path triggers
        # (error key present → repair condition fires) rather than aborting the turn.
        return {"error": "query_failed", "reason": str(exc)}
    return {"columns": columns, "rows": [list(r) for r in fetched]}


def build_paperhub_sql_server() -> FastMCP:
    """Construct a FastMCP server exposing the three read-only SQL tools.

    The server's ``streamable_http_path`` is set to ``/`` so mounting at
    ``/mcp/sql`` (via the shared mount helper) makes ``POST /mcp/sql`` the
    streamable-HTTP transport endpoint — matching the convention every other
    MCP server entry in ``mcp_servers.toml`` uses.
    """
    server = FastMCP(SQL_SERVER_NAME, streamable_http_path="/")
    server.settings.json_response = True
    server.settings.stateless_http = True
    server.add_tool(
        _list_tables_handler,
        name="list_tables",
        description="List the read-only tables you may query (the allowlist).",
    )
    server.add_tool(
        _describe_handler,
        name="describe",
        description="Return [{name, type}] columns for one allowlisted table.",
    )
    server.add_tool(
        _query_handler,
        name="query",
        description=(
            "Run ONE read-only SQL statement (SELECT or WITH...SELECT) over the "
            "allowlisted tables. Returns {columns, rows}. Writes/DDL are rejected."
        ),
    )
    return server
