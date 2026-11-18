"""Per-server MCP connector (SRS v2.5, §III-6.1).

`MCPClient` wraps the official Anthropic `mcp` Python SDK
(`mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`)
behind a small surface tailored to PaperHub's Research Agent dispatch:

* `connect()` is idempotent and uses exponential backoff (cap 4 attempts)
  before raising `MCPUnavailableError` for transport-level failures.
* `list_tools()` returns LiteLLM-shaped JSON-schema tool dicts with the
  upstream tool names namespaced (`<server>.<tool>`), filtered by the
  `expose` allowlist, with `aliases` applied.
* `call_tool(name, args)` accepts either the original upstream name *or*
  its alias (the registry strips the `<server>.` prefix before calling),
  enforces the per-server `timeout_seconds`, distinguishes transport
  failures (`MCPUnavailableError`) from upstream tool errors
  (`MCPToolError`).

Only the `streamable_http` transport is wired in v2.5-1; `stdio` raises
`NotImplementedError`. Plan E wires the stdio path when it adds the
sqlite MCP server block.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession
from mcp.client.streamable_http import GetSessionIdCallback, streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.message import SessionMessage

from .client_context import current_client_headers_context
from .config import MCPServerConfig
from .errors import MCPToolError, MCPUnavailableError

__all__ = ["MCPClient"]

_LOG = logging.getLogger(__name__)

_Streams = tuple[MemoryObjectReceiveStream[SessionMessage | Exception],
                 MemoryObjectSendStream[SessionMessage], GetSessionIdCallback]


@asynccontextmanager
async def streamablehttp_client(
    url: str, headers: dict[str, str] | None = None,
) -> AsyncIterator[_Streams]:
    """The streamable-HTTP transport with ``headers`` on every request:
    mcp's ``streamable_http_client`` over an httpx client this owns (the
    timeouts of mcp's deprecated ``streamablehttp_client``, 30 s and a
    5 min read for the event stream). Tests patch this name."""
    client = create_mcp_http_client(
        headers=headers, timeout=httpx.Timeout(30.0, read=300.0))
    async with client, streamable_http_client(url, http_client=client) as streams:
        yield streams

# Exponential backoff schedule (seconds) for transport reconnect. Cap 4
# attempts total → 3 sleeps between them. Kept small so a permanently
# unreachable server doesn't block FastAPI startup for more than ~1s.
_BACKOFF_SCHEDULE: tuple[float, ...] = (0.1, 0.3, 0.7)


def _unwrap_connect_error(exc: BaseException) -> str:
    """Render a connection error in operator-readable form.

    The streamable-HTTP transport runs under an anyio TaskGroup, which
    surfaces failures as ``BaseExceptionGroup("unhandled errors in a
    TaskGroup (1 sub-exception)", [...])`` — the outer wrapper hides the
    real cause (typically ``ConnectionRefusedError: [WinError 1225] The
    remote computer refused the network connection`` when the daemon is
    down). Walk single-child exception groups down to the leaf so the
    WARN log + final ``MCPUnavailableError`` actually tell operators what
    happened.
    """
    cur: BaseException = exc
    # ExceptionGroup / BaseExceptionGroup are Python 3.11+ types; both
    # expose `.exceptions`. We only unwrap when there's exactly one child
    # — multi-child groups mean genuinely-disjoint failures and the
    # operator should see them all.
    while (
        isinstance(cur, BaseExceptionGroup)
        and len(cur.exceptions) == 1
    ):
        cur = cur.exceptions[0]
    return f"{type(cur).__name__}: {cur}" if str(cur) else type(cur).__name__


class MCPClient:
    """One connector per configured MCP server.

    The `connect()` lifetime owns an `AsyncExitStack` that holds open the
    underlying streamable-HTTP transport and the `ClientSession`. They
    are released together by `disconnect()` (or by a failed `connect()`
    retry loop).
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._connected = False
        # Headers the current session was opened with (bound to the
        # underlying httpx client by `streamablehttp_client`). Compared
        # against the per-request `ClientHeadersContext` on every
        # `call_tool` so a chat request with a different session_id
        # transparently reconnects rather than reusing the previous
        # request's session header. ``None`` means "connected without a
        # contextvar set" (operator smoke path).
        self._session_headers: dict[str, str] | None = None
        # Reverse alias map: exposed-name → upstream-name, populated lazily.
        self._reverse_aliases: dict[str, str] = {
            exposed: upstream for upstream, exposed in config.aliases.items()
        }
        # Serializes drift-refresh reconnects. The registry caches one
        # `MCPClient` per server name across every FastAPI request, and
        # each request runs on its own asyncio task — without this lock,
        # two concurrent requests whose contextvars both drift from the
        # live session can interleave their tear-down/reopen and end up
        # calling `session.call_tool` on a stack the sibling just closed.
        # Created lazily on first use so the lock binds to whatever event
        # loop actually drives the client, not whatever loop (if any) was
        # current when the registry constructed the client.
        self._refresh_lock: asyncio.Lock | None = None

    @property
    def name(self) -> str:
        """Server namespace (the `<server>.` prefix on aggregated tool names)."""
        return self._config.name

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ lifecycle

    async def connect(self) -> None:
        """Open the transport + session. Idempotent.

        Retries transport errors with exponential backoff, capped at 4
        attempts. Raises `MCPUnavailableError` after the final failure
        or for any unsupported transport. Raises `NotImplementedError`
        for `stdio` (deferred to Plan E).
        """
        if self._connected:
            return

        if self._config.transport == "stdio":
            raise NotImplementedError(
                "stdio MCP transport is not wired in v2.5-1; ships in Plan E "
                "alongside the sqlite MCP server"
            )

        if self._config.transport != "streamable_http":
            # Defensive — config loader already rejects this, but keep
            # the dispatcher honest.
            raise MCPUnavailableError(
                f"unsupported MCP transport {self._config.transport!r} for "
                f"server {self._config.name!r}"
            )

        url = self._config.url
        assert url is not None  # guaranteed by config loader

        last_exc: BaseException | None = None
        attempts = len(_BACKOFF_SCHEDULE) + 1  # 4 total
        for attempt in range(attempts):
            if attempt > 0:
                await asyncio.sleep(_BACKOFF_SCHEDULE[attempt - 1])
            try:
                await self._open_session(url)
                self._connected = True
                _LOG.info("mcp.connect ok server=%s url=%s", self._config.name, url)
                return
            except BaseException as exc:  # noqa: BLE001 — re-raised below
                last_exc = exc
                _LOG.warning(
                    "mcp.connect attempt %d/%d failed server=%s err=%s",
                    attempt + 1,
                    attempts,
                    self._config.name,
                    _unwrap_connect_error(exc),
                )
                # Ensure any half-opened stack is released before retry.
                await self._close_stack_silently()

        assert last_exc is not None
        raise MCPUnavailableError(
            f"could not connect to MCP server {self._config.name!r} at {url} "
            f"after {attempts} attempts: {_unwrap_connect_error(last_exc)}"
        ) from last_exc

    async def disconnect(self) -> None:
        """Close the transport + session. Safe to call when not connected."""
        was_connected = self._connected
        await self._close_stack_silently()
        self._connected = False
        if was_connected:
            _LOG.info("mcp.disconnect ok server=%s", self._config.name)

    async def _open_session(self, url: str) -> None:
        stack = AsyncExitStack()
        try:
            headers = _build_outbound_headers()
            read, write, _get_session_id = await stack.enter_async_context(
                streamablehttp_client(url, headers=headers)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self._session_headers = headers

    async def _close_stack_silently(self) -> None:
        stack = self._stack
        self._stack = None
        self._session = None
        self._session_headers = None
        if stack is None:
            return
        try:
            await stack.aclose()
        except Exception as exc:  # noqa: BLE001
            _LOG.debug(
                "mcp.disconnect cleanup raised (suppressed) server=%s err=%s",
                self._config.name,
                exc,
            )

    # ------------------------------------------------------------------ public ops

    async def list_tools(self) -> list[dict[str, Any]]:
        """Fetch the upstream `tools/list` and reshape for LiteLLM.

        Returned schemas are namespaced (`<server>.<tool>`), filtered by
        `MCPServerConfig.expose`, and have `MCPServerConfig.aliases`
        applied to the final tool name.
        """
        await self._refresh_session_headers_if_drifted()
        session = self._require_session()
        result = await session.list_tools()

        expose = set(self._config.expose)
        out: list[dict[str, Any]] = []
        for tool in result.tools:
            if tool.name not in expose:
                continue
            exposed_name = self._config.aliases.get(tool.name, tool.name)
            namespaced = f"{self._config.name}.{exposed_name}"
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": namespaced,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema or {"type": "object"},
                    },
                }
            )
        return out

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Invoke an upstream tool by its un-namespaced name.

        ``name`` may be either the original upstream name (e.g.
        ``fetchWebContent``) or its alias (e.g. ``fetch``); the client
        translates the alias back before the wire call.

        Returns:
            ``structuredContent`` if the upstream provided it, else the
            concatenated text of the result's content blocks.

        Raises:
            MCPUnavailableError: on timeout or transport failure.
            MCPToolError: if the tool is not exposed, or the upstream
                response has ``isError=True``.

        Per-request header refresh: if the current
        :class:`ClientHeadersContext` carries a different ``session_id`` /
        ``run_id`` than the live session was opened with, the session is
        torn down and reopened so the new headers reach the server. The
        underlying `streamablehttp_client` binds headers to its httpx
        client at construction time, so the only way to swap is to
        reconnect.
        """
        await self._refresh_session_headers_if_drifted()
        session = self._require_session()

        upstream_name = self._reverse_aliases.get(name, name)
        if upstream_name not in self._config.expose:
            raise MCPToolError(
                f"unknown tool {name!r} for MCP server {self._config.name!r} "
                f"(exposed: {sorted(self._config.expose)})"
            )

        try:
            result = await asyncio.wait_for(
                session.call_tool(upstream_name, arguments=args),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as exc:
            _LOG.warning(
                "mcp.call_tool timeout server=%s tool=%s timeout=%.1fs",
                self._config.name,
                name,
                self._config.timeout_seconds,
            )
            raise MCPUnavailableError(
                f"MCP tool {self._config.name}.{name} timed out after "
                f"{self._config.timeout_seconds:.1f}s"
            ) from exc
        except MCPToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "mcp.call_tool transport error server=%s tool=%s err=%s",
                self._config.name,
                name,
                exc,
            )
            raise MCPUnavailableError(
                f"MCP tool {self._config.name}.{name} transport error: {exc}"
            ) from exc

        text_parts = [
            getattr(block, "text", "")
            for block in (result.content or [])
            if getattr(block, "type", None) == "text"
        ]
        joined_text = "\n".join(p for p in text_parts if p)

        if getattr(result, "isError", False):
            raise MCPToolError(
                f"MCP tool {self._config.name}.{name} returned error: "
                f"{joined_text or '<no message>'}"
            )

        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured
        return joined_text

    def _require_session(self) -> ClientSession:
        if not self._connected or self._session is None:
            raise MCPUnavailableError(
                f"MCP client for server {self._config.name!r} is not connected; "
                "call connect() first"
            )
        return self._session

    async def _refresh_session_headers_if_drifted(self) -> None:
        """If the live session was opened with different headers than the
        current :class:`ClientHeadersContext` would produce, tear it down
        and reconnect.

        Necessary because the streamable-HTTP transport binds headers to
        its httpx client at construction time — there is no per-RPC
        override. Different chat requests share the same registry-cached
        :class:`MCPClient`; without this refresh, the second chat request
        would send the first request's ``X-Paperhub-Session-Id``.

        No-op when the client is not connected (the next ``connect()``
        will pick up the current contextvar naturally), or when the
        headers already match (the hot path: same request running multiple
        tool calls in sequence).

        Concurrency: the drift-check + reconnect is serialized under
        ``self._refresh_lock`` so two concurrent FastAPI requests sharing
        the registry-cached client cannot interleave a tear-down + reopen
        (one task closing the stack the other just refreshed). A
        double-checked locking pattern keeps the fast path lock-free when
        headers already match, and lets a second waiter that already has
        the right headers skip the redundant reconnect after the first
        waiter releases the lock.
        """
        if not self._connected:
            return
        desired = _build_outbound_headers()
        if desired == self._session_headers:
            return
        if self._refresh_lock is None:
            # Bind to the running loop on first use; safe because we
            # already awaited (no synchronous race possible here under
            # cooperative scheduling).
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            # Re-check after acquiring the lock: a sibling task may have
            # just reconnected with headers that happen to match ours.
            desired = _build_outbound_headers()
            if desired == self._session_headers:
                return
            _LOG.debug(
                "mcp.client refreshing session headers server=%s old=%s new=%s",
                self._config.name,
                self._session_headers,
                desired,
            )
            url = self._config.url
            assert url is not None
            # `_connected` stays True across the swap so a sibling task
            # reading `_connected` (e.g. another `call_tool`'s fast-path
            # `if not self._connected: return`) doesn't observe the
            # in-flight tear-down/reopen as "disconnected". The lock
            # already excludes another refresh from running concurrently;
            # holding `_connected=True` is the right surface for the
            # `_require_session` check that runs after this returns.
            await self._close_stack_silently()
            try:
                await self._open_session(url)
            except BaseException:
                # Reopen failed — the live state is inconsistent
                # (closed stack, `_session=None`). Surface that to the
                # caller via `_require_session` on the next op.
                self._connected = False
                raise


def _build_outbound_headers() -> dict[str, str] | None:
    """Read the per-request :class:`ClientHeadersContext` and return the
    headers dict to attach to the outbound streamable-HTTP POST, or
    ``None`` when no context is set.

    Headers are bound to the underlying httpx client at
    `streamablehttp_client` construction time (there is no per-RPC
    override), so the connection is opened with whatever this function
    returns at that moment.  When a subsequent ``call_tool`` runs under a
    different :class:`ClientHeadersContext` (typical for the
    registry-cached MCPClient serving multiple chat requests in one
    process), :meth:`MCPClient._refresh_session_headers_if_drifted` tears
    the session down and reopens it so the new context wins.
    """
    ctx = current_client_headers_context()
    if ctx is None:
        return None
    headers: dict[str, str] = {"X-Paperhub-Session-Id": str(ctx.session_id)}
    if ctx.run_id is not None:
        headers["X-Paperhub-Run-Id"] = str(ctx.run_id)
    return headers
