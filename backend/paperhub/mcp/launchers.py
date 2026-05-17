"""MCP dispatcher factory — connects Phase A tools to real upstream servers.

Phase A tools
-------------
* ``arxiv``  — launched as ``uvx arxiv-mcp-server`` subprocess via the MCP
  SDK's stdio transport.  Provides ``get_abstract``, ``download_paper``,
  ``search_papers``.
* ``grobid`` — launched in-process as a FastMCP server backed by
  :mod:`paperhub.mcp.tools.grobid_server`.  Provides ``process_header``,
  ``process_fulltext``.

Lifespan ownership (D3 fix)
---------------------------
:class:`LaunchedMcpSessions` owns the MCP subprocess sessions for the
lifetime of the FastAPI app.  It must be used as an ``async with`` context
manager inside the FastAPI ``lifespan`` hook so that:

1. The subprocess is started once on startup (not per-request).
2. Context managers are entered and exited in the *same* asyncio task, which
   avoids the anyio cancel-scope mismatch crash on GC finalisation.

Error handling (D2 fix)
-----------------------
:meth:`_ArxivSession.call` now checks ``result.isError`` AND inspects the
``status`` field of the parsed JSON content.  Both trigger
:exc:`McpUpstreamError`.

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
import json
import logging
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

from paperhub.mcp.client import McpDispatcher
from paperhub.mcp.scopes import GrobidProcessFulltextArgs, GrobidProcessHeaderArgs, McpInvocation

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias for a lazily-resolved upstream handler
# ---------------------------------------------------------------------------

_ToolHandler = Callable[[McpInvocation], Awaitable[dict[str, object]]]


class McpUpstreamError(RuntimeError):
    """Raised when the upstream MCP server returns an error response."""

    def __init__(self, invocation: McpInvocation, message: str) -> None:
        super().__init__(f"Upstream MCP error for {invocation.tool}/{invocation.method}: {message}")
        self.invocation = invocation
        self.upstream_message = message


class _ArxivSession:
    """Stdio session for ``arxiv-mcp-server`` (uvx subprocess).

    When used via :class:`LaunchedMcpSessions`, the session is started once
    during app lifespan and shared across all requests.  The context managers
    are entered and exited in the same asyncio task to avoid the anyio
    cancel-scope mismatch (D3 fix).

    Direct use (lazy connect) is still supported for backwards-compatibility
    with call sites that don't use the lifespan manager.
    """

    def __init__(self, arxiv_command: str = "uvx arxiv-mcp-server") -> None:
        self._lock = asyncio.Lock()
        self._session: Any = None
        # Keep references to the context managers so they aren't GC'd.
        self._stdio_ctx: Any = None
        self._session_ctx: Any = None
        self._arxiv_command = arxiv_command
        # When True the session was entered via __aenter__ and must be exited
        # via __aexit__ in the same task.
        self._owned: bool = False

    async def __aenter__(self) -> _ArxivSession:
        """Enter the MCP subprocess context in the current asyncio task."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command_parts = shlex.split(self._arxiv_command)
        command = command_parts[0]
        args = command_parts[1:] if len(command_parts) > 1 else []

        params = StdioServerParameters(command=command, args=args, env=None)
        self._stdio_ctx = stdio_client(params)
        read_stream, write_stream = await self._stdio_ctx.__aenter__()

        session: Any = ClientSession(read_stream, write_stream)
        self._session_ctx = session
        await session.__aenter__()
        await session.initialize()

        self._session = session
        self._owned = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit the MCP subprocess context in the same task it was entered."""
        if self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(*exc_info)
            except Exception:
                log.exception("Error exiting ClientSession context")
        if self._stdio_ctx is not None:
            try:
                await self._stdio_ctx.__aexit__(*exc_info)
            except Exception:
                log.exception("Error exiting stdio_client context")
        self._session = None
        self._owned = False

    async def _ensure_connected(self) -> Any:
        """Start the subprocess on first call; return cached session thereafter.

        Used when the session is NOT owned by a lifespan context manager (e.g.
        in tests that construct a dispatcher directly).  In production the
        lifespan manager calls ``__aenter__`` instead.
        """
        async with self._lock:
            if self._session is not None:
                return self._session
            # Lazy connect — late import so tests that inject a fake dispatcher
            # never touch this path.
            await self.__aenter__()
            return self._session

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        """Dispatch a single MCP tool call and return the parsed result.

        Raises McpUpstreamError if the upstream returns an error (D2 fix):
        - ``result.isError`` is True in the MCP envelope
        - OR the parsed JSON content has ``{"status": "error", ...}``
        """
        session = await self._ensure_connected()

        # Map our internal method names to upstream tool names + arg dicts.
        # Upstream tool surface (blazickjp/arxiv-mcp-server):
        #   get_abstract(paper_id)  → metadata
        #   download_paper(paper_id) → {status, content} (markdown)
        #   search_papers(query, ...) → list of matches
        method = invocation.method
        args_dict = invocation.args.model_dump()

        # Remap arg keys to upstream expectations
        if method == "get_abstract":
            upstream_tool = "get_abstract"
            upstream_args = {"paper_id": args_dict.get("paper_id", args_dict.get("arxiv_id", ""))}
        elif method == "download_paper":
            upstream_tool = "download_paper"
            upstream_args = {"paper_id": args_dict.get("paper_id", args_dict.get("arxiv_id", ""))}
        elif method == "search_papers":
            upstream_tool = "search_papers"
            upstream_args = args_dict
        else:
            # Pass through unknown methods unchanged (forward-compat)
            upstream_tool = method
            upstream_args = args_dict

        result = await session.call_tool(upstream_tool, upstream_args)

        # --- D2: check isError on the MCP envelope ---
        if getattr(result, "isError", False):
            contents = getattr(result, "content", [])
            error_text = contents[0].text if contents else "unknown upstream error"
            raise McpUpstreamError(invocation, error_text)

        # Extract the first text content block
        contents = getattr(result, "content", [])
        if not contents:
            return {"result": result}

        text = getattr(contents[0], "text", None)
        if text is None:
            return {"result": result}

        # --- D2: check status field in the parsed JSON content ---
        try:
            parsed: dict[str, object] = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("status") == "error":
                error_msg = str(parsed.get("message", text))
                raise McpUpstreamError(invocation, error_msg)
            return {"result": text, **parsed}
        except (json.JSONDecodeError, TypeError):
            # Plain text response (not JSON) — return as-is
            return {"result": text}


class _ArxivLatexSession:
    """Stdio session for ``arxiv-latex-mcp`` (uvx subprocess).

    Provides lossless LaTeX source access for arXiv papers (Tier 1 of the
    §1.1 source-fidelity ladder).  The tool surface (takashiishida/arxiv-latex-mcp):
      - get_paper_prompt(arxiv_id)    → full flattened LaTeX source as plain text
      - get_paper_abstract(arxiv_id)  → abstract (possibly including title/authors)
      - list_paper_sections(arxiv_id) → section headings
      - get_paper_section(arxiv_id, section_path) → specific section text

    Lifecycle mirrors :class:`_ArxivSession`: use via :class:`LaunchedMcpSessions`
    for the lifespan-owned path, or lazy-connect for per-request use.
    """

    def __init__(self, command: str = "uvx arxiv-latex-mcp") -> None:
        self._lock = asyncio.Lock()
        self._session: Any = None
        self._stdio_ctx: Any = None
        self._session_ctx: Any = None
        self._command = command
        self._owned: bool = False

    async def __aenter__(self) -> _ArxivLatexSession:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command_parts = shlex.split(self._command)
        command = command_parts[0]
        args = command_parts[1:] if len(command_parts) > 1 else []

        params = StdioServerParameters(command=command, args=args, env=None)
        self._stdio_ctx = stdio_client(params)
        read_stream, write_stream = await self._stdio_ctx.__aenter__()

        session: Any = ClientSession(read_stream, write_stream)
        self._session_ctx = session
        await session.__aenter__()
        await session.initialize()

        self._session = session
        self._owned = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(*exc_info)
            except Exception:
                log.exception("Error exiting arxiv-latex ClientSession context")
        if self._stdio_ctx is not None:
            try:
                await self._stdio_ctx.__aexit__(*exc_info)
            except Exception:
                log.exception("Error exiting arxiv-latex stdio_client context")
        self._session = None
        self._owned = False

    async def _ensure_connected(self) -> Any:
        async with self._lock:
            if self._session is not None:
                return self._session
            await self.__aenter__()
            return self._session

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        """Dispatch a single arxiv-latex-mcp tool call.

        Maps our internal method names to upstream tool names.  The upstream
        tool names are snake_case matching the README surface exactly.

        Raises McpUpstreamError on any upstream error (isError envelope or
        status=error JSON content).
        """
        session = await self._ensure_connected()

        method = invocation.method
        args_dict = invocation.args.model_dump()

        # Map method → upstream tool name + args
        if method == "get_paper_prompt":
            upstream_tool = "get_paper_prompt"
            upstream_args = {"arxiv_id": args_dict.get("arxiv_id", "")}
        elif method == "get_paper_abstract":
            upstream_tool = "get_paper_abstract"
            upstream_args = {"arxiv_id": args_dict.get("arxiv_id", "")}
        elif method == "list_paper_sections":
            upstream_tool = "list_paper_sections"
            upstream_args = {"arxiv_id": args_dict.get("arxiv_id", "")}
        elif method == "get_paper_section":
            upstream_tool = "get_paper_section"
            upstream_args = {
                "arxiv_id": args_dict.get("arxiv_id", ""),
                "section_path": args_dict.get("section_path", ""),
            }
        else:
            # Forward-compat passthrough
            upstream_tool = method
            upstream_args = args_dict

        result = await session.call_tool(upstream_tool, upstream_args)

        if getattr(result, "isError", False):
            contents = getattr(result, "content", [])
            error_text = contents[0].text if contents else "unknown upstream error"
            raise McpUpstreamError(invocation, error_text)

        contents = getattr(result, "content", [])
        if not contents:
            return {"result": result}

        text = getattr(contents[0], "text", None)
        if text is None:
            return {"result": result}

        try:
            parsed: dict[str, object] = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("status") == "error":
                error_msg = str(parsed.get("message", text))
                raise McpUpstreamError(invocation, error_msg)
            return {"result": text, **parsed}
        except (json.JSONDecodeError, TypeError):
            # Plain text response (the LaTeX source is NOT JSON)
            return {"result": text}


class _GrobidSession:
    """In-process FastMCP session for the GROBID wrapper."""

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        # Import the tool functions directly so we don't need a real MCP
        # transport in Phase A (avoids spinning up a local TCP server).
        from paperhub.mcp.tools.grobid_server import (
            process_fulltext,
            process_header,
        )

        args = invocation.args
        if isinstance(args, GrobidProcessHeaderArgs):
            method = "process_header"
            pdf_path = str(args.pdf_path)
        elif isinstance(args, GrobidProcessFulltextArgs):
            method = "process_fulltext"
            pdf_path = str(args.pdf_path)
        else:
            raise TypeError(f"Unexpected args type for grobid: {type(args).__name__}")

        if method == "process_header":
            result = process_header(pdf_path)
        else:
            result = process_fulltext(pdf_path)

        return {"tei": result}


# ---------------------------------------------------------------------------
# D3: Lifespan-owned MCP sessions
# ---------------------------------------------------------------------------


class LaunchedMcpSessions:
    """Owns long-lived MCP sessions for the entire FastAPI app lifespan.

    Usage in ``api/app.py`` lifespan::

        async with LaunchedMcpSessions(settings) as sessions:
            dispatcher = sessions.make_dispatcher()
            if dispatcher is not None:
                app.state.mcp_dispatcher = dispatcher
            yield

    The arxiv session's context managers are entered and exited in the same
    asyncio task (the lifespan task), which avoids the anyio cancel-scope
    mismatch crash (D3 fix).

    If the subprocess fails to start, ``make_dispatcher()`` returns ``None``
    and callers fall back to the per-request ``make_dispatcher()`` function
    (which uses lazy connect).
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._arxiv = _ArxivSession(arxiv_command=settings.mcp_arxiv_command)
        self._arxiv_latex = _ArxivLatexSession(
            command=settings.mcp_arxiv_latex_command
        )
        self._grobid = _GrobidSession()
        self._arxiv_started: bool = False
        self._arxiv_latex_started: bool = False

    @property
    def _started(self) -> bool:
        """True when at least the Tier-3 arxiv session is running."""
        return self._arxiv_started

    async def __aenter__(self) -> LaunchedMcpSessions:
        # Start Tier-3 arxiv-mcp-server (existing fallback)
        log.info("Starting arXiv MCP subprocess: %s", self._settings.mcp_arxiv_command)
        try:
            await self._arxiv.__aenter__()
            self._arxiv_started = True
            log.info("arXiv MCP session started successfully")
        except Exception:
            log.warning(
                "arXiv MCP subprocess failed to start — make_dispatcher() will return None; "
                "routes will fall back to per-request lazy connect",
                exc_info=True,
            )

        # Start Tier-1 arxiv-latex-mcp (LaTeX source, preferred path)
        log.info(
            "Starting arxiv-latex-mcp subprocess: %s",
            self._settings.mcp_arxiv_latex_command,
        )
        try:
            await self._arxiv_latex.__aenter__()
            self._arxiv_latex_started = True
            log.info("arxiv-latex-mcp session started successfully")
        except Exception:
            log.warning(
                "arxiv-latex-mcp subprocess failed to start — Tier 1 will fall back "
                "to per-request lazy connect (or skip to Tier 3 if unavailable)",
                exc_info=True,
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._arxiv_latex_started:
            await self._arxiv_latex.__aexit__(*exc_info)
        if self._arxiv_started:
            await self._arxiv.__aexit__(*exc_info)

    def make_dispatcher(self) -> McpDispatcher | None:
        """Return a dispatcher backed by the pre-launched sessions, or None if not started.

        Returns None when the subprocess failed to start so callers can fall
        back to the per-request ``make_dispatcher()`` factory (lazy connect).

        Routing:
          ``arxiv``       → _ArxivSession       (Tier 3: arxiv-mcp-server markdown)
          ``arxiv_latex`` → _ArxivLatexSession  (Tier 1: arxiv-latex-mcp LaTeX source)
          ``grobid``      → _GrobidSession      (in-process)
        """
        if not self._arxiv_started:
            return None
        arxiv_call = self._arxiv.call
        arxiv_latex_call = self._arxiv_latex.call
        grobid_call = self._grobid.call

        _ROUTES: dict[str, _ToolHandler] = {
            "arxiv": arxiv_call,
            "arxiv_latex": arxiv_latex_call,
            "grobid": grobid_call,
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


# ---------------------------------------------------------------------------
# make_dispatcher — per-request factory (kept for backwards-compat / tests)
# ---------------------------------------------------------------------------


def make_dispatcher(
    scopes: dict[str, Any] | None = None,  # reserved for future scope filtering
    settings: Any = None,
) -> McpDispatcher:
    """Return a :data:`McpDispatcher` that routes calls to the correct upstream.

    Parameters
    ----------
    scopes:
        Reserved for future per-tool scope filtering at the dispatcher level.
        Currently unused — scope enforcement happens in
        :class:`~paperhub.mcp.client.McpClient`.
    settings:
        A :class:`~paperhub.config.Settings` instance. If not provided, uses
        :func:`~paperhub.config.get_settings()`. Supplies the ``mcp_arxiv_command``
        for the subprocess dispatcher.

    Returns
    -------
    McpDispatcher
        An async callable ``(McpInvocation) -> dict[str, object]``.

    Note
    ----
    In production, prefer :class:`LaunchedMcpSessions` (managed by the app
    lifespan) over this function to avoid the anyio cancel-scope mismatch.
    This function creates a lazy ``_ArxivSession`` that enters its context
    managers per-call, which can cause issues on GC finalization (D3).
    """
    if settings is None:
        from paperhub.config import get_settings

        settings = get_settings()

    _arxiv = _ArxivSession(arxiv_command=settings.mcp_arxiv_command)
    _arxiv_latex = _ArxivLatexSession(command=settings.mcp_arxiv_latex_command)
    _grobid = _GrobidSession()

    _ROUTES: dict[str, _ToolHandler] = {
        "arxiv": _arxiv.call,
        "arxiv_latex": _arxiv_latex.call,
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
