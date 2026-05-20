"""Process-wide MCP server registry (SRS v2.5, §III-6.1; v2.6 lazy-connect;
v2.6 follow-up: config-driven subprocess autostart).

`MCPRegistry` is constructed once per FastAPI process. ``startup()`` loads
``mcp_servers.toml`` and **constructs** one :class:`MCPClient` per
``[[server]]`` block, but does NOT open the transport yet. Connection is
lazy on first tool use (``aggregate_tool_schemas`` or ``call``) — this
sidesteps the loopback-bootstrap race where the ``papers`` server (Task
v2.5-3) points at the backend's own port, which is not yet accepting
connections during lifespan startup.

A server that fails to connect is remembered as failed and skipped on
subsequent palette lookups, but the failure is RETRIED after a 30-second
cooldown (``_RETRY_AFTER_SECONDS``). This handles the slow-startup race
without hammering a permanently-dead daemon: e.g. ``npx open-websearch``
sometimes takes 30-60s on a cold first run (package download + Playwright
init), and an early lazy-connect would otherwise sticky-fail the server
for the whole backend lifecycle.

**Autostart (config-driven, fallback path).** A ``[[server]]`` block may
declare a ``launch`` list (e.g. ``["npx", "-y", "open-websearch@latest"]``).
At ``startup()`` the registry TCP-probes the configured URL; if no daemon is
listening, it spawns the launch command (with merged ``launch_env``) via a
**detached** ``subprocess.Popen`` (see ``launcher.launch_detached`` — NOT the
asyncio subprocess API, which raises ``NotImplementedError`` on the
``SelectorEventLoop`` uvicorn forces under ``--reload`` on Windows) and polls
until the daemon is reachable. Spawned daemons are **detach-and-leak**: they
are NOT terminated on ``shutdown()``, so a worker reload doesn't kill + respawn
them (an ``npx`` cold start is ~25s) — identical posture to the model server.
The supported path is ``scripts/start.ps1``, which pre-starts every
``launch``-declaring server via ``paperhub-mcp-up``; this in-worker autostart
is the fallback for a bare ``uvicorn`` run. ``npx -y`` auto-fetches the package
on first boot, so fresh-clone developers need nothing pre-installed.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from .client import MCPClient
from .config import MCPServerConfig, load_mcp_servers
from .errors import MCPUnavailableError
from .launcher import (
    host_port_from_url,
    launch_detached,
    tcp_reachable,
    terminate,
    wait_until_reachable,
)

# Re-export under the historical private name so existing imports/tests
# (tests/mcp/test_registry_probe.py) keep resolving through the registry.
_tcp_reachable = tcp_reachable

__all__ = ["MCPRegistry"]

_LOG = logging.getLogger(__name__)


class MCPRegistry:
    """Owns the per-process map of MCP server name → :class:`MCPClient`.

    Connection is lazy: ``startup()`` constructs clients, the first
    ``aggregate_tool_schemas()`` or ``call()`` triggers their ``connect()``.
    """

    # Cooldown after a failed connect/list_tools before we retry the
    # server. Without this, a transient startup race (daemon slow to
    # come up, e.g. npx download + Playwright init) sticks the server
    # in _connect_failed for the entire backend lifecycle — operators
    # have to restart the whole process for the agent to see the tool.
    # 30s is short enough to recover within one or two chat turns,
    # long enough that a truly-dead daemon isn't probed on every
    # tool-palette lookup.
    _RETRY_AFTER_SECONDS: float = 30.0

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._connect_attempted: set[str] = set()
        self._connect_failed: set[str] = set()
        # Server name → monotonic time of last failure. Used to gate
        # the retry-after-cooldown path.
        self._failed_at: dict[str, float] = {}
        self._aggregated_schemas: list[dict[str, Any]] | None = None
        # Server name → live subprocess we spawned at startup. Detach-and-leak:
        # NOT terminated on `shutdown()` (so reloads don't thrash the daemon);
        # populated only for configs with `has_launch`. See module docstring.
        self._launched: dict[str, subprocess.Popen[bytes]] = {}
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
        """Disconnect every client. Spawned daemons are **detach-and-leak**.

        We intentionally do NOT terminate ``self._launched`` here: under
        ``uvicorn --reload`` the worker is torn down on every code edit, and
        killing the daemon each time would re-pay the ~25s ``npx`` cold start
        on the next boot. The daemon outlives the worker (it's detached) and
        is reused via the reachability probe — same posture as the model
        server. Explicit teardown is the boot script's job (``start.ps1``
        terminates ``paperhub-mcp-up``); leaked daemons otherwise clear at OS
        reboot or manual kill.
        """
        for name, client in self._clients.items():
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("mcp.registry disconnect failed server=%s err=%s", name, exc)
        self._aggregated_schemas = None

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
        host, port = host_port_from_url(cfg.url)
        if host is None or port is None:
            _LOG.warning(
                "mcp.registry %s: cannot parse host:port from url=%r; skipping launch",
                cfg.name, cfg.url,
            )
            return

        if await tcp_reachable(host, port):
            _LOG.info(
                "mcp.registry %s daemon already reachable on %s:%d; skipping launch",
                cfg.name, host, port,
            )
            return

        # Detached subprocess.Popen (loop-independent — works on the
        # SelectorEventLoop uvicorn forces under --reload on Windows). PATH /
        # spawn failures are logged inside launch_detached and surface as
        # None; the unreachable server then fails cleanly at first call_tool.
        proc = launch_detached(cfg.launch, cfg.launch_env, label=cfg.name)
        if proc is None:
            return
        self._launched[cfg.name] = proc

        if not await wait_until_reachable(host, port, cfg.launch_ready_timeout):
            _LOG.warning(
                "mcp.registry %s: daemon never became reachable on %s:%d "
                "within %.1fs; terminating subprocess (pid=%s)",
                cfg.name, host, port, cfg.launch_ready_timeout, proc.pid,
            )
            terminate(proc)
            self._launched.pop(cfg.name, None)
            return

        _LOG.info(
            "mcp.registry %s daemon ready on %s:%d (pid=%s)",
            cfg.name, host, port, proc.pid,
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
        now = time.monotonic()
        # If any previously-failed server has cooled down past the
        # retry window, drop it from the failure set AND invalidate
        # the aggregated cache so the loop below re-probes it. This
        # is what lets a slow-to-start daemon (npx download +
        # Playwright init) eventually enter the palette without
        # restarting the backend.
        retry_candidates = [
            n for n in self._connect_failed
            if now - self._failed_at.get(n, 0.0) >= self._RETRY_AFTER_SECONDS
        ]
        if retry_candidates:
            for n in retry_candidates:
                self._connect_failed.discard(n)
                self._connect_attempted.discard(n)
                self._failed_at.pop(n, None)
                _LOG.info(
                    "mcp.registry retrying previously-failed server=%s "
                    "after %.0fs cooldown", n, self._RETRY_AFTER_SECONDS,
                )
            self._aggregated_schemas = None

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
                    self._failed_at[name] = time.monotonic()
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
                    self._failed_at[name] = time.monotonic()
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
