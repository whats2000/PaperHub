"""MCP dispatcher factory — connects Phase A tools to real upstream servers.

Phase A tools
-------------
* ``arxiv``  — launched as ``uvx arxiv-mcp-server`` subprocess via the MCP
  SDK's stdio transport.  Provides ``search``, ``fetch_metadata``,
  ``download_pdf``.
* ``grobid`` — launched in-process as a FastMCP server backed by
  :mod:`paperhub.mcp.tools.grobid_server`.  Provides ``process_header``,
  ``process_fulltext``.

Lazy launch
-----------
Sessions are created on the first call to each tool and cached for the
lifetime of the dispatcher.  This avoids spawning subprocesses at import time
and keeps startup fast.

Routing
-------
Each :class:`~paperhub.mcp.scopes.McpInvocation` carries a ``tool`` field
(``"arxiv"`` or ``"grobid"``).  ``make_dispatcher`` returns a single async
callable that routes to the correct upstream session.

Tests
-----
Do NOT test subprocess launch in Task 6 — those are end-to-end smoke tests
in Task 8 (``tests/integration/test_paper_qa_e2e.py``, ``@pytest.mark.e2e``).
Task 6 tests inject a fake dispatcher and only verify the routing *signature*.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from paperhub.mcp.client import McpDispatcher
from paperhub.mcp.scopes import McpInvocation

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias for a lazily-resolved upstream handler
# ---------------------------------------------------------------------------

_ToolHandler = Callable[[McpInvocation], Awaitable[dict[str, object]]]


class _ArxivSession:
    """Lazy stdio session for ``arxiv-mcp-server`` (uvx subprocess).

    The subprocess is started on the first call and the ``ClientSession``
    kept alive for the duration of the process.  Cleanup is intentionally
    deferred to process exit — Phase A does not need graceful teardown.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session: Any = None
        # Keep references to the context managers so they aren't GC'd.
        self._stdio_ctx: Any = None
        self._session_ctx: Any = None

    async def _ensure_connected(self) -> Any:
        """Start the subprocess on first call; return cached session thereafter."""
        async with self._lock:
            if self._session is not None:
                return self._session

            # Late import: tests that inject a fake dispatcher never touch this path.
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command="uvx",
                args=["arxiv-mcp-server"],
                env=None,
            )
            # Enter the stdio_client context manager to get the (read, write) streams.
            self._stdio_ctx = stdio_client(params)
            read_stream, write_stream = await self._stdio_ctx.__aenter__()

            # Enter the ClientSession context manager to initialise the session.
            session: Any = ClientSession(read_stream, write_stream)
            self._session_ctx = session
            await session.__aenter__()
            await session.initialize()

            self._session = session
            return session

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        session = await self._ensure_connected()
        result = await session.call_tool(invocation.method, invocation.args.model_dump())
        # MCP SDK returns a CallToolResult; extract the first text content.
        contents = getattr(result, "content", [])
        if contents:
            text = getattr(contents[0], "text", None)
            if text is not None:
                return {"result": text}
        return {"result": result}


class _GrobidSession:
    """In-process FastMCP session for the GROBID wrapper."""

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        # Import the tool functions directly so we don't need a real MCP
        # transport in Phase A (avoids spinning up a local TCP server).
        from paperhub.mcp.tools.grobid_server import (
            process_fulltext,
            process_header,
        )

        args_dict = invocation.args.model_dump()
        pdf_path: str = str(args_dict.get("path", args_dict.get("pdf_path", "")))

        if invocation.method == "process_header":
            result = process_header(pdf_path)
        elif invocation.method == "process_fulltext":
            result = process_fulltext(pdf_path)
        else:
            raise ValueError(f"Unknown grobid method: {invocation.method!r}")

        return {"tei": result}


def make_dispatcher(
    scopes: dict[str, Any] | None = None,  # reserved for future scope filtering
    settings: Any = None,  # reserved for future settings plumbing
) -> McpDispatcher:
    """Return a :data:`McpDispatcher` that routes calls to the correct upstream.

    Parameters
    ----------
    scopes:
        Reserved for future per-tool scope filtering at the dispatcher level.
        Currently unused — scope enforcement happens in
        :class:`~paperhub.mcp.client.McpClient`.
    settings:
        Reserved for future configuration (e.g. GROBID host/port).
        Currently unused.

    Returns
    -------
    McpDispatcher
        An async callable ``(McpInvocation) -> dict[str, object]``.
    """
    _arxiv = _ArxivSession()
    _grobid = _GrobidSession()

    _ROUTES: dict[str, _ToolHandler] = {
        "arxiv": _arxiv.call,
        "grobid": _grobid.call,
    }

    async def dispatch(invocation: McpInvocation) -> dict[str, object]:
        handler = _ROUTES.get(invocation.tool)
        if handler is None:
            raise ValueError(
                f"No upstream handler registered for tool {invocation.tool!r}. "
                f"Registered tools: {sorted(_ROUTES)}"
            )
        return await handler(invocation)

    return dispatch
