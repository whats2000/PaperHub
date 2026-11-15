"""``paperhub-mcp-up`` — ensure every launchable MCP server is running.

The boot script (``scripts/start.ps1``) runs this once before uvicorn so that
fresh-clone developers get the external MCP daemons (today: ``open-websearch``;
later: ``sql`` / ``fs`` / ``paperhub.*``) without installing or hand-starting
anything. It is the supported counterpart to the in-worker registry autostart
fallback — same launch primitive (detached ``subprocess.Popen`` via
``mcp.launcher``), driven by the same ``mcp_servers.toml``.

For every ``[[server]]`` block with a ``launch`` command:
  * probe the configured URL — already reachable? leave it (we don't own it);
  * otherwise spawn it detached and poll until it binds (or its
    ``launch_ready_timeout`` elapses).

Detach-and-leak by design: spawned daemons OUTLIVE this short-lived CLI so they
survive ``uvicorn --reload`` and are reused on the next boot via the probe.
**Lifecycle handoff**: the ports of daemons we actually STARTED (not the ones
already running, which we don't own) are written to a sidecar file next to
``mcp_servers.toml`` so the boot script (``scripts/start.ps1``) can tree-kill
them in its ``finally`` — otherwise Ctrl+C on the boot script stops the
backend but leaves the MCP daemon running. Always exits 0: a
daemon that won't start is non-fatal (the agent falls back), so it must not
fail the boot script.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
from pathlib import Path

from paperhub.config import load_settings
from paperhub.mcp.config import (
    MCPServerConfig,
    ensure_config_seeded,
    load_mcp_servers,
    resolve_config_path,
)
from paperhub.mcp.launcher import (
    host_port_from_url,
    launch_detached,
    tcp_reachable,
    terminate,
    wait_until_reachable,
)
from paperhub.outbound.policy import load_policy, locked_keys

_LOG = logging.getLogger("paperhub.mcp_up")

# Sidecar file (next to mcp_servers.toml) listing the ports of daemons THIS run
# started. scripts/start.ps1 reads it to tree-kill them on Ctrl+C. Gitignored
# alongside the toml; rewritten every run.
_PORTS_FILENAME = ".mcp_daemon_ports"


def _env_with_settings_overlay() -> dict[str, str]:
    """os.environ plus the unlocked rows of the workspace ``settings`` table, the
    same precedence the backend applies at boot (a locked key stays env-only), so
    a Settings-panel choice also steers what this launcher starts."""
    env = dict(os.environ)
    try:
        con = sqlite3.connect(load_settings().db_path, timeout=5.0)
        try:
            rows = con.execute("SELECT key, value FROM settings").fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return env  # no DB yet / no table: env alone
    locked = locked_keys(env)
    for key, value in rows:
        if key not in locked and isinstance(value, str):
            env[key] = value
    return env


def _ports_file() -> Path:
    return resolve_config_path().with_name(_PORTS_FILENAME)


async def _ensure_one(cfg: MCPServerConfig) -> str:
    """Probe → skip-if-up → launch-and-wait. Returns a one-word status."""
    assert cfg.url is not None  # has_launch implies streamable_http (config loader)
    host, port = host_port_from_url(cfg.url)
    if host is None or port is None:
        _LOG.warning("%s: cannot parse host:port from url=%r", cfg.name, cfg.url)
        return "bad-url"

    if await tcp_reachable(host, port):
        _LOG.info("%s already reachable on %s:%d; leaving it", cfg.name, host, port)
        return "already-running"

    proc = launch_detached(cfg.launch, cfg.launch_env, label=cfg.name)
    if proc is None:
        return "launch-failed"  # PATH/spawn failure already logged by launcher

    if await wait_until_reachable(host, port, cfg.launch_ready_timeout):
        _LOG.info("%s started on %s:%d (pid=%d)", cfg.name, host, port, proc.pid)
        return f"started(pid={proc.pid})"

    _LOG.warning(
        "%s did not bind %s:%d within %.1fs; terminating spawn",
        cfg.name, host, port, cfg.launch_ready_timeout,
    )
    terminate(proc)
    return "timeout"


async def _amain() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    path = resolve_config_path()
    ensure_config_seeded(path)
    if not path.exists():
        _LOG.info("no MCP config at %s; nothing to launch", path)
        return 0

    configs = load_mcp_servers(path)
    web_on = load_policy(_env_with_settings_overlay()).public_web_search
    skipped = [c.name for c in configs if c.has_launch and c.public_web and not web_on]
    if skipped:
        _LOG.info("public web search is off; not launching: %s", ", ".join(skipped))
    launchable = [c for c in configs if c.has_launch and (web_on or not c.public_web)]
    if not launchable:
        _LOG.info("no launchable MCP servers in %s (none declare `launch`)", path.name)
        return 0

    _LOG.info(
        "ensuring %d launchable MCP server(s): %s",
        len(launchable), ", ".join(c.name for c in launchable),
    )
    statuses = await asyncio.gather(*[_ensure_one(c) for c in launchable])

    # Record the ports of daemons we STARTED (not already-running ones — those
    # belong to whoever launched them). The boot script tree-kills these on
    # exit. Rewritten every run; cleared when we started nothing this run.
    started_ports: list[int] = []
    for cfg, status in zip(launchable, statuses, strict=True):
        _LOG.info("  %-12s %s", cfg.name, status)
        if status.startswith("started") and cfg.url is not None:
            _host, port = host_port_from_url(cfg.url)
            if port is not None:
                started_ports.append(port)
    _write_ports_file(started_ports)
    return 0


def _write_ports_file(ports: list[int]) -> None:
    """Persist the ports we started for the boot script's cleanup pass."""
    path = _ports_file()
    try:
        if ports:
            path.write_text("\n".join(str(p) for p in ports) + "\n", encoding="utf-8")
        elif path.exists():
            path.unlink()  # nothing started this run → no stale ports to clean
    except OSError as exc:
        _LOG.warning("could not write %s: %s", path.name, exc)


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_amain()))
