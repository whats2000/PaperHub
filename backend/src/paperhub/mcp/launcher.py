"""Shared launch + reachability helpers for external MCP servers.

Both the in-worker registry autostart (``MCPRegistry._maybe_launch``) and the
``paperhub-mcp-up`` boot CLI need the same three primitives: probe a configured
``host:port``, spawn a ``launch`` command when it's down, and poll until the
daemon binds. Centralised here so the subtle bits live in one place:

  * **Windows IPv4-first probe.** ``localhost`` resolves to ``::1`` FIRST on
    Windows, but Node servers (``open-websearch``) bind IPv4 only — probing the
    IPv6 address first hangs until timeout and the autostart wrongly declares
    the daemon dead. We iterate every ``getaddrinfo`` result, IPv4 first.
  * **Detached ``subprocess.Popen`` (NOT ``asyncio.create_subprocess_exec``).**
    uvicorn's ``--reload`` (and ``workers > 1``) force a ``SelectorEventLoop``
    on Windows, which raises ``NotImplementedError`` on the asyncio subprocess
    API. ``subprocess.Popen`` needs no event-loop subprocess support, so it
    works on either loop — the same reason the model server spawns this way.
    Children are detached (Windows ``CREATE_NEW_PROCESS_GROUP`` / Unix
    ``start_new_session``) so a worker reload doesn't take them down with it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import subprocess
import sys
from urllib.parse import urlparse

__all__ = [
    "host_port_from_url",
    "launch_detached",
    "tcp_reachable",
    "terminate",
    "wait_until_reachable",
]

_LOG = logging.getLogger(__name__)

# Per-probe TCP-connect timeout.
_PROBE_CONNECT_TIMEOUT = 0.5
# Polling cadence while waiting for a freshly-spawned daemon to bind.
_LAUNCH_POLL_INTERVAL = 0.5
# Time budget for a SIGTERM'd subprocess to exit cleanly before SIGKILL.
_TERMINATE_GRACE_S = 5.0


def host_port_from_url(url: str) -> tuple[str | None, int | None]:
    """Parse ``host``/``port`` from a streamable-HTTP URL (scheme-default port)."""
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if port is None and parsed.scheme in {"http", "https"}:
        port = 80 if parsed.scheme == "http" else 443
    return host, port


async def tcp_reachable(host: str, port: int) -> bool:
    """Cheap probe: TCP-connect to any address ``host`` resolves to.

    Resolves ``host`` via :func:`socket.getaddrinfo` and tries each address
    (IPv4 preferred) in turn. Returns True on the first successful connect.
    Closes the probe socket immediately — streamable-HTTP MCP servers tolerate
    a closed probe. See the module docstring for the Windows IPv4-first reason.

    This bug was caught by **live-backend testing**, not pytest — a real
    socket-roundtrip test lives in ``tests/mcp/test_registry_probe.py``.
    """
    import socket as _socket

    loop = asyncio.get_event_loop()
    try:
        addrinfo = await loop.getaddrinfo(host, port, type=_socket.SOCK_STREAM)
    except _socket.gaierror:
        return False

    # Prefer IPv4 (loopback servers on Windows / Node bind IPv4 only by
    # default). Stable-sort so the order is otherwise preserved.
    addrinfo.sort(key=lambda ai: 0 if ai[0] == _socket.AF_INET else 1)

    for family, _socktype, _proto, _canon, sockaddr in addrinfo:
        try:
            _reader, writer = await asyncio.wait_for(
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


async def wait_until_reachable(host: str, port: int, deadline_after: float) -> bool:  # noqa: ASYNC109 — operator-facing knob from MCPServerConfig.launch_ready_timeout
    """Poll ``host:port`` until it accepts a connect, up to ``deadline_after`` s."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_after
    while loop.time() < deadline:
        if await tcp_reachable(host, port):
            return True
        await asyncio.sleep(_LAUNCH_POLL_INTERVAL)
    return False


def _wrap_for_windows_shim(exe: str, args: list[str]) -> list[str]:
    """On Windows, ``npx`` / ``yarn`` / ``pnpm`` resolve to ``.cmd`` shims that
    ``subprocess`` cannot run directly. Route them through ``cmd /c`` so the
    shim launches its target."""
    if sys.platform.startswith("win") and exe.lower().endswith(".cmd"):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


def launch_detached(
    launch: list[str],
    launch_env: dict[str, str],
    *,
    label: str = "",
) -> subprocess.Popen[bytes] | None:
    """Spawn ``launch`` (with merged ``launch_env``) as a detached subprocess.

    Returns the :class:`subprocess.Popen` handle, or ``None`` (logged) when the
    binary isn't on PATH or the spawn itself fails — both non-fatal: the daemon
    simply doesn't come up and the caller falls back.

    stdout/stderr go to ``DEVNULL`` (detached children can't share our pipes
    without risking a full-buffer block). Operators who want the daemon's logs
    run it in the foreground via ``scripts/start.ps1`` / a second shell.
    """
    tag = f"{label}: " if label else ""
    if not launch:
        return None
    exe = shutil.which(launch[0])
    if exe is None:
        _LOG.info(
            "mcp.launcher %slaunch binary %r not on PATH; cannot autostart "
            "(install it or run `%s` manually)",
            tag, launch[0], " ".join(launch),
        )
        return None

    argv = _wrap_for_windows_shim(exe, launch[1:])
    env = {**os.environ, **launch_env}
    _LOG.info(
        "mcp.launcher %sspawning launch=%s env-overrides=%s",
        tag, argv, sorted(launch_env),
    )
    try:
        # Platform-specific detach flag — split call sites (rather than
        # **kwargs) because Popen's overloads are narrow on these kwargs and
        # mypy can't unify them through a generic dict.
        if sys.platform == "win32":
            proc = subprocess.Popen(  # noqa: S603 — argv from operator config
                argv, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            proc = subprocess.Popen(  # noqa: S603 — argv from operator config
                argv, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except (OSError, ValueError) as exc:
        _LOG.warning(
            "mcp.launcher %sspawn failed (%s: %s); skipping. Run it manually: %s",
            tag, type(exc).__name__, exc, " ".join(launch),
        )
        return None
    return proc


def terminate(proc: subprocess.Popen[bytes] | None) -> None:
    """Best-effort terminate of a :func:`launch_detached` child — and its whole
    process **tree**.

    Critical: the child is typically ``cmd /c npx … open-websearch`` (Windows)
    or a shell wrapper that itself forks ``npx → node → the daemon``. A plain
    ``proc.terminate()`` kills only the wrapper and ORPHANS the node daemon
    that actually binds the port (observed: the "terminated" daemon kept
    running and bound :3000 anyway). So we kill the entire tree:

      * Windows: ``taskkill /T /F`` walks + kills descendants by PID.
      * POSIX: we spawned with ``start_new_session`` so the child leads a new
        process group — ``killpg`` takes the npx/node children with it.
    """
    if proc is None or proc.poll() is not None:
        return

    if sys.platform == "win32":
        try:
            subprocess.run(  # noqa: S603, S607 — fixed taskkill args, PID from our own child
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=_TERMINATE_GRACE_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _LOG.warning("mcp.launcher taskkill failed for pid=%d: %s", proc.pid, exc)
            with contextlib.suppress(OSError):
                proc.kill()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            proc.wait(timeout=_TERMINATE_GRACE_S)
        return

    # POSIX: kill the child's process group (it's a session/group leader).
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=_TERMINATE_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            pass
        os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_TERMINATE_GRACE_S)
    except (ProcessLookupError, PermissionError) as exc:
        _LOG.warning("mcp.launcher killpg failed for pid=%d: %s", proc.pid, exc)
