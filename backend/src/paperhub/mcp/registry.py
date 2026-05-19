"""Process-wide MCP server registry (SRS v2.5, §III-6.1; v2.6 lazy-connect;
v2.6 follow-up: config-driven subprocess autostart).

`MCPRegistry` is constructed once per FastAPI process. ``startup()`` loads
``mcp_servers.toml`` and **constructs** one :class:`MCPClient` per
``[[server]]`` block, but does NOT open the transport yet. Connection is
lazy on first tool use (``aggregate_tool_schemas`` or ``call``) — this
sidesteps the loopback-bootstrap race where the ``papers`` server (Task
v2.5-3) points at the backend's own port, which is not yet accepting
connections during lifespan startup.

A server that fails to connect on its first attempt is **remembered as
failed** for the rest of the registry lifecycle — we don't want a 30-tool
palette query to hammer a permanently-dead server. Operators restart the
backend to retry.

**Autostart (config-driven).** A ``[[server]]`` block may declare a
``launch`` list (e.g. ``["npx", "-y", "open-websearch@latest", "serve"]``).
At ``startup()`` the registry TCP-probes the configured URL; if no daemon
is listening, it spawns the launch command (with merged ``launch_env``)
and polls until the daemon is reachable or ``launch_ready_timeout``
elapses. Spawned subprocesses are tracked and terminated on ``shutdown()``
so the daemon dies with the backend. Fresh-clone developers don't need to
install or pre-run anything — ``npx -y`` auto-fetches the package on
first boot.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import MCPClient
from .config import MCPServerConfig, load_mcp_servers
from .errors import MCPUnavailableError

__all__ = ["MCPRegistry"]

_LOG = logging.getLogger(__name__)

# Time budget for a SIGTERM'd subprocess to exit cleanly before SIGKILL.
_SUBPROCESS_TERMINATE_TIMEOUT = 5.0
# Polling cadence while waiting for a freshly-spawned daemon to bind.
_LAUNCH_POLL_INTERVAL = 0.5
# Per-probe TCP-connect timeout.
_PROBE_CONNECT_TIMEOUT = 0.5


class MCPRegistry:
    """Owns the per-process map of MCP server name → :class:`MCPClient`.

    Connection is lazy: ``startup()`` constructs clients, the first
    ``aggregate_tool_schemas()`` or ``call()`` triggers their ``connect()``.
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._connect_attempted: set[str] = set()
        self._connect_failed: set[str] = set()
        self._aggregated_schemas: list[dict[str, Any]] | None = None
        # Server name → live subprocess we spawned at startup. Terminated
        # on `shutdown()`; populated only for configs with `has_launch`.
        self._launched: dict[str, asyncio.subprocess.Process] = {}
        # v2.7 follow-up: serialise aggregate_tool_schemas + _ensure_
        # connected so concurrent callers (the fan-out per ParsedRequest
        # via asyncio.gather) don't race on the connect path. Without
        # this, Task B sees ``_connect_attempted.add(name)`` from Task A
        # and returns immediately while Task A's ``await client.connect()``
        # is still in flight — list_tools then raises "not connected"
        # and the server gets sticky-failed forever. Lazy-init so the
        # lock binds to the running loop on first use.
        self._connect_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------ lifecycle

    async def startup(self, config_path: Path) -> None:
        """Load ``mcp_servers.toml`` and construct clients (no connect).

        Missing config file is non-fatal (fresh-clone-friendly): logs INFO
        and returns with an empty registry.
        """
        if not config_path.exists():  # noqa: ASYNC240
            _LOG.info(
                "mcp.registry no config at %s; starting with empty registry",
                config_path,
            )
            return

        try:
            configs = load_mcp_servers(config_path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "mcp.registry failed to load config %s err=%s; starting empty",
                config_path,
                exc,
            )
            return

        for cfg in configs:
            self._clients[cfg.name] = MCPClient(cfg)
        _LOG.info(
            "mcp.registry loaded %d server config(s): %s",
            len(self._clients),
            sorted(self._clients),
        )

        # Spawn subprocesses for any server with `launch` set, in parallel.
        # Failures are absorbed into log lines — they don't block backend
        # boot or the rest of the registry. The unreachable server's
        # `connect()` will fail normally on first use and land in
        # `_connect_failed` via the existing sticky-fail path.
        launchable = [cfg for cfg in configs if cfg.has_launch]
        if launchable:
            await asyncio.gather(
                *[self._maybe_launch(cfg) for cfg in launchable],
                return_exceptions=False,
            )

    async def shutdown(self) -> None:
        """Disconnect every client + terminate spawned subprocesses."""
        for name, client in self._clients.items():
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("mcp.registry disconnect failed server=%s err=%s", name, exc)
        self._aggregated_schemas = None
        await self._terminate_launched()

    # ------------------------------------------------------------------ autostart

    async def _maybe_launch(self, cfg: MCPServerConfig) -> None:
        """Spawn ``cfg.launch`` if the configured URL isn't already serving.

        Operates only on ``streamable_http`` configs (enforced by
        ``config._parse_block``). On any failure path, logs WARN and returns
        so the rest of the registry keeps booting — the unreachable server
        will surface as ``MCPUnavailableError`` at the agent's first
        ``call_tool``, which the dispatcher already handles cleanly.
        """
        assert cfg.url is not None  # enforced by config loader
        host, port = _host_port_from_url(cfg.url)
        if host is None or port is None:
            _LOG.warning(
                "mcp.registry %s: cannot parse host:port from url=%r; skipping launch",
                cfg.name, cfg.url,
            )
            return

        if await _tcp_reachable(host, port):
            _LOG.info(
                "mcp.registry %s daemon already reachable on %s:%d; skipping launch",
                cfg.name, host, port,
            )
            return

        cmd = list(cfg.launch)
        if not cmd:
            return
        exe = shutil.which(cmd[0])
        if exe is None:
            _LOG.info(
                "mcp.registry %s: launch binary %r not on PATH; cannot autostart "
                "(install it or run `%s` manually)",
                cfg.name, cmd[0], " ".join(cmd),
            )
            return

        env = {**os.environ, **cfg.launch_env}
        argv = _wrap_for_windows_shim(exe, cmd[1:])
        _LOG.info(
            "mcp.registry %s spawning launch=%s env-overrides=%s",
            cfg.name, argv, sorted(cfg.launch_env),
        )
        try:
            proc = await asyncio.create_subprocess_exec(*argv, env=env)
        except OSError as exc:
            _LOG.warning(
                "mcp.registry %s: subprocess spawn failed (%s); skipping launch",
                cfg.name, exc,
            )
            return
        self._launched[cfg.name] = proc

        if not await _wait_until_reachable(host, port, deadline_after=cfg.launch_ready_timeout):
            _LOG.warning(
                "mcp.registry %s: daemon never became reachable on %s:%d "
                "within %.1fs; terminating subprocess (pid=%s)",
                cfg.name, host, port, cfg.launch_ready_timeout, proc.pid,
            )
            await _terminate(proc)
            self._launched.pop(cfg.name, None)
            return

        _LOG.info(
            "mcp.registry %s daemon ready on %s:%d (pid=%s)",
            cfg.name, host, port, proc.pid,
        )

    async def _terminate_launched(self) -> None:
        """Terminate every subprocess we spawned. Parallel for fast shutdown."""
        if not self._launched:
            return
        items = list(self._launched.items())
        self._launched.clear()
        await asyncio.gather(
            *[self._terminate_one(name, proc) for name, proc in items],
            return_exceptions=False,
        )

    async def _terminate_one(
        self, name: str, proc: asyncio.subprocess.Process,
    ) -> None:
        try:
            await _terminate(proc)
            _LOG.info("mcp.registry %s subprocess terminated (pid=%s)", name, proc.pid)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "mcp.registry %s subprocess termination error pid=%s err=%s",
                name, proc.pid, exc,
            )

    # ------------------------------------------------------------------ public ops

    async def aggregate_tool_schemas(self) -> list[dict[str, Any]]:
        """Union of every reachable server's LiteLLM tool schemas.

        On first call, lazy-connects every configured client. Servers that
        fail to connect are logged at WARN and skipped — and **not retried**
        within this registry lifecycle. Cached after the first successful
        build; the cache is invalidated by :meth:`call` on transport failure.

        **Concurrency**: serialised by ``self._connect_lock``. The v2.7
        fan-out runs N ParsedRequests through ``asyncio.gather``; each
        branch's first ``has_tool``/``aggregate_tool_schemas`` call
        would otherwise race on ``_ensure_connected`` — Task B observed
        Task A's ``_connect_attempted.add(name)`` and skipped the
        ``await client.connect()`` while it was still in flight, then
        called ``list_tools`` on an unconnected client. The lock makes
        the connect happen exactly once and lets concurrent callers
        share the result.
        """
        if self._aggregated_schemas is not None:
            return self._aggregated_schemas
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            # Re-check inside the lock: another concurrent caller may have
            # built the cache while we waited.
            if self._aggregated_schemas is not None:
                return self._aggregated_schemas

            aggregated: list[dict[str, Any]] = []
            for name, client in self._clients.items():
                if name in self._connect_failed:
                    continue
                await self._ensure_connected(name, client)
                if name in self._connect_failed:
                    continue
                try:
                    schemas = await client.list_tools()
                except MCPUnavailableError as exc:
                    _LOG.warning(
                        "mcp.registry list_tools failed server=%s err=%s; skipping",
                        name,
                        exc,
                    )
                    self._connect_failed.add(name)
                    continue
                aggregated.extend(schemas)

            self._aggregated_schemas = aggregated
            return aggregated

    async def has_tool(self, namespaced_name: str) -> bool:
        """Convenience: is ``namespaced_name`` in the aggregated palette?"""
        schemas = await self.aggregate_tool_schemas()
        return any(s["function"]["name"] == namespaced_name for s in schemas)

    async def call(self, namespaced_name: str, args: dict[str, Any]) -> Any:
        """Dispatch ``<server>.<tool>`` to the right client.

        On :class:`MCPUnavailableError` during dispatch, invalidates the
        cached palette so the next ``aggregate_tool_schemas`` call re-checks.
        :class:`MCPToolError` is propagated without invalidating the cache
        (the connection is healthy; the upstream tool just errored).
        """
        if "." not in namespaced_name:
            raise ValueError(
                f"expected namespaced tool name '<server>.<tool>', got {namespaced_name!r}"
            )
        server_name, tool_name = namespaced_name.split(".", 1)

        client = self._clients.get(server_name)
        if client is None:
            raise MCPUnavailableError(f"no server named {server_name!r}")

        # Serialise the connect via the same lock aggregate_tool_schemas
        # uses — two concurrent ``call`` invocations for the same
        # server (e.g. fan-out per ParsedRequest) would otherwise race
        # ``_ensure_connected`` (Task B observes Task A's
        # ``_connect_attempted.add`` and returns while connect is in
        # flight). Note ``MCPClient.connect`` is itself idempotent, so
        # acquiring the lock when the connect already finished is a
        # quick no-op on the second call.
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            await self._ensure_connected(server_name, client)
        if server_name in self._connect_failed:
            self._aggregated_schemas = None
            raise MCPUnavailableError(
                f"MCP server {server_name!r} is unreachable; cannot dispatch "
                f"{namespaced_name!r}"
            )

        try:
            return await client.call_tool(tool_name, args)
        except MCPUnavailableError:
            self._aggregated_schemas = None
            raise

    # ------------------------------------------------------------------ internals

    async def _ensure_connected(self, name: str, client: MCPClient) -> None:
        """Lazy-connect ``client`` if we haven't tried yet this lifecycle.

        Sticky-fail: a server that failed on its first attempt is recorded
        in ``self._connect_failed`` and never retried.
        """
        if name in self._connect_attempted:
            return
        self._connect_attempted.add(name)
        try:
            await client.connect()
        except MCPUnavailableError as exc:
            _LOG.warning(
                "mcp.registry connect failed server=%s err=%s; skipping",
                name,
                exc,
            )
            self._connect_failed.add(name)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "mcp.registry unexpected connect error server=%s err=%s; skipping",
                name,
                exc,
            )
            self._connect_failed.add(name)


# ===================================================================== helpers


def _host_port_from_url(url: str) -> tuple[str | None, int | None]:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if port is None and parsed.scheme in {"http", "https"}:
        port = 80 if parsed.scheme == "http" else 443
    return host, port


async def _tcp_reachable(host: str, port: int) -> bool:
    """Cheap probe: TCP-connect to any address ``host`` resolves to.

    Resolves ``host`` via :func:`socket.getaddrinfo` and tries each
    address (IPv4 + IPv6) in turn. Returns True on the first successful
    connect. Closes the probe socket immediately — streamable-HTTP MCP
    servers tolerate a closed probe.

    **Windows / dual-stack gotcha.** On Windows the loopback resolver
    returns ``::1`` (IPv6) FIRST for ``localhost``, but `npx open-
    websearch` binds IPv4 only. ``asyncio.open_connection("localhost",
    port)`` then tries ``::1`` first; if it hangs instead of refusing
    fast (common on Windows where IPv6 connectivity *appears* present),
    every probe times out at ``_PROBE_CONNECT_TIMEOUT`` and the autostart
    declares the daemon unreachable — even though IPv4 ``127.0.0.1:PORT``
    is happily serving. Explicit per-family iteration avoids that.

    This bug was caught by **live-backend testing**, not pytest — the
    unit tests stubbed `_tcp_reachable` and so couldn't see it. A real
    socket-roundtrip test lives in `tests/mcp/test_registry_probe.py`.
    """
    import socket as _socket

    loop = asyncio.get_event_loop()
    try:
        addrinfo = await loop.getaddrinfo(
            host, port, type=_socket.SOCK_STREAM,
        )
    except _socket.gaierror:
        return False

    # Prefer IPv4 (loopback servers on Windows / Node bind IPv4 only by
    # default). Stable-sort so the order is otherwise preserved.
    addrinfo.sort(key=lambda ai: 0 if ai[0] == _socket.AF_INET else 1)

    for family, _socktype, _proto, _canon, sockaddr in addrinfo:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=sockaddr[0], port=sockaddr[1], family=family,
                ),
                timeout=_PROBE_CONNECT_TIMEOUT,
            )
        except (OSError, TimeoutError):
            continue
        writer.close()
        with contextlib.suppress(Exception):
            # Closing a probe socket; benign races on the response side.
            await writer.wait_closed()
        return True
    return False


async def _wait_until_reachable(host: str, port: int, deadline_after: float) -> bool:  # noqa: ASYNC109 — operator-facing knob from MCPServerConfig.launch_ready_timeout
    """Poll ``host:port`` until it accepts a TCP connect, up to ``deadline_after`` seconds."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_after
    while loop.time() < deadline:
        if await _tcp_reachable(host, port):
            return True
        await asyncio.sleep(_LAUNCH_POLL_INTERVAL)
    return False


def _wrap_for_windows_shim(exe: str, args: list[str]) -> list[str]:
    """On Windows, ``npx`` / ``yarn`` / ``pnpm`` resolve to ``.cmd`` shims
    that ``asyncio.create_subprocess_exec`` cannot run directly. Route them
    through ``cmd /c`` so the shim launches its target."""
    if sys.platform.startswith("win") and exe.lower().endswith(".cmd"):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Graceful SIGTERM, then SIGKILL after ``_SUBPROCESS_TERMINATE_TIMEOUT``."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(
            proc.wait(), timeout=_SUBPROCESS_TERMINATE_TIMEOUT,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
