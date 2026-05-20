"""MCP server config dataclass + TOML loader (SRS v2.5, §III-6 / §III-6.1).

`mcp_servers.toml` is the operator-edited list of MCP servers the registry
should connect to at FastAPI startup. Each `[[server]]` block becomes one
`MCPServerConfig`, which is consumed by `MCPClient` (Task v2.5-1) and the
`MCPRegistry` (Task v2.5-2).

Schema (see `mcp_servers.toml.example`):

    [[server]]
    name = "web"                   # namespace prefix → "web.search"
    transport = "streamable_http"  # or "stdio" (dispatch not yet wired)
    url = "http://localhost:3000/mcp"   # streamable_http only
    command = "npx"                # stdio only
    args = ["-y", "pkg", "..."]    # stdio only
    expose = ["search", "fetchWebContent"]
    aliases = { "fetchWebContent" = "fetch" }
    timeout_seconds = 8.0
    # Optional: how to spawn the daemon when the URL probe fails at
    # startup. The registry detects an unreachable streamable_http URL
    # and runs `launch` (with merged `launch_env`), then polls until the
    # daemon is reachable or `launch_ready_timeout` elapses. Tracked
    # subprocesses are terminated on FastAPI shutdown.
    launch = ["npx", "-y", "open-websearch@latest", "serve"]
    launch_env = { PORT = "3000", MODE = "both" }
    launch_ready_timeout = 15.0
"""
from __future__ import annotations

import logging
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "MCPServerConfig",
    "Transport",
    "ensure_config_seeded",
    "load_mcp_servers",
    "resolve_config_path",
]

_LOG = logging.getLogger(__name__)

Transport = Literal["streamable_http", "stdio"]
_VALID_TRANSPORTS: tuple[Transport, ...] = ("streamable_http", "stdio")
_DEFAULT_TIMEOUT_SECONDS = 8.0
_DEFAULT_LAUNCH_READY_TIMEOUT = 15.0


@dataclass(frozen=True)
class MCPServerConfig:
    """Per-server MCP connector config.

    Exactly one of `url` (streamable_http) or `command` (stdio) must be
    populated, enforced by `load_mcp_servers`. `expose` is the allowlist
    of upstream tool names — anything not in the list is hidden from the
    LiteLLM tool palette. `aliases` is an upstream-name → exposed-name
    rename map applied after the allowlist filter (e.g. rename verbose
    upstream names like `fetchWebContent` to a tidier `fetch`).

    `timeout_seconds` is the per-call upper bound enforced by
    `MCPClient.call_tool` — exceeded calls raise `MCPUnavailableError`
    so the agent dispatch layer treats them like any transport failure.
    """

    name: str
    transport: Transport
    expose: list[str]
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    launch: list[str] = field(default_factory=list)
    launch_env: dict[str, str] = field(default_factory=dict)
    launch_ready_timeout: float = _DEFAULT_LAUNCH_READY_TIMEOUT

    @property
    def has_launch(self) -> bool:
        """Whether the registry should spawn this server when unreachable."""
        return bool(self.launch)


def load_mcp_servers(path: Path) -> list[MCPServerConfig]:
    """Parse + validate `mcp_servers.toml`.

    Raises:
        FileNotFoundError: if `path` does not exist.
        ValueError: on any schema violation. The message identifies the
            offending block by index (e.g. ``server[0]: missing 'name'``).
    """
    if not path.exists():
        raise FileNotFoundError(f"MCP servers config not found: {path}")

    with path.open("rb") as f:
        raw = tomllib.load(f)

    blocks: list[dict[str, Any]] = raw.get("server", []) or []
    if not isinstance(blocks, list):
        raise ValueError("'server' must be an array of tables ([[server]])")

    out: list[MCPServerConfig] = []
    for idx, block in enumerate(blocks):
        out.append(_parse_block(idx, block))
    return out


def resolve_config_path() -> Path:
    """Resolve ``mcp_servers.toml``. Env override → backend repo sibling.

    Shared by the FastAPI app (``app._lifespan``) and the ``paperhub-mcp-up``
    CLI so both read the same file. ``PAPERHUB_MCP_CONFIG`` wins; otherwise
    the file sits next to ``backend/`` (config.py is
    ``backend/src/paperhub/mcp/config.py`` → ``parents[3]`` is ``backend``).
    """
    env = os.environ.get("PAPERHUB_MCP_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "mcp_servers.toml"


def ensure_config_seeded(path: Path) -> None:
    """Seed ``mcp_servers.toml`` from the checked-in ``.example`` on first boot.

    The ``papers`` server is REQUIRED by the Research Agent (no in-process
    fallback), so a fresh clone with no config would silently boot with an
    empty tool palette. Auto-seeding from the example closes that gap; the
    operator can still edit afterwards. Skips when the file already exists
    (operator-customised) or the example is missing (env-overridden path).
    """
    if path.exists():
        return
    example = path.with_name(path.name + ".example")
    if not example.exists():
        _LOG.info(
            "mcp.config %s absent + no example at %s; "
            "registry/launcher will start empty",
            path.name, example,
        )
        return
    shutil.copyfile(example, path)
    _LOG.info("mcp.config seeded %s from %s (first-boot default)", path.name, example.name)


def _parse_block(idx: int, block: dict[str, Any]) -> MCPServerConfig:
    prefix = f"server[{idx}]"
    if not isinstance(block, dict):
        raise ValueError(f"{prefix}: must be a table")

    name = block.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{prefix}: missing or invalid 'name' (expected non-empty string)")

    transport = block.get("transport")
    if transport is None:
        raise ValueError(f"{prefix}: missing 'transport' (expected one of {_VALID_TRANSPORTS})")
    if transport not in _VALID_TRANSPORTS:
        raise ValueError(
            f"{prefix}: unknown 'transport' {transport!r} "
            f"(expected one of {_VALID_TRANSPORTS})"
        )

    expose_raw = block.get("expose")
    if not isinstance(expose_raw, list) or not all(isinstance(t, str) for t in expose_raw):
        raise ValueError(
            f"{prefix}: 'expose' must be a list of upstream tool name strings"
        )
    expose: list[str] = list(expose_raw)

    url = block.get("url")
    command = block.get("command")
    args_raw = block.get("args", [])
    if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
        raise ValueError(f"{prefix}: 'args' must be a list of strings")
    args: list[str] = list(args_raw)

    if transport == "streamable_http":
        if not isinstance(url, str) or not url:
            raise ValueError(
                f"{prefix}: 'streamable_http' transport requires a non-empty 'url'"
            )
        if command is not None:
            raise ValueError(
                f"{prefix}: 'command' is only valid with transport='stdio'"
            )
    else:  # stdio
        if not isinstance(command, str) or not command:
            raise ValueError(
                f"{prefix}: 'stdio' transport requires a non-empty 'command'"
            )
        if url is not None:
            raise ValueError(
                f"{prefix}: 'url' is only valid with transport='streamable_http'"
            )

    aliases_raw = block.get("aliases", {})
    if not isinstance(aliases_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in aliases_raw.items()
    ):
        raise ValueError(f"{prefix}: 'aliases' must be a string→string map")
    aliases: dict[str, str] = dict(aliases_raw)

    # Every alias key must be present in the expose list — otherwise the
    # operator wrote an alias for a tool that will never be reached.
    expose_set = set(expose)
    for upstream in aliases:
        if upstream not in expose_set:
            raise ValueError(
                f"{prefix}: alias key {upstream!r} is not in 'expose' "
                f"({sorted(expose_set)})"
            )

    timeout_raw = block.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout_raw, (int, float)) or isinstance(timeout_raw, bool):
        raise ValueError(f"{prefix}: 'timeout_seconds' must be a number")
    timeout_seconds = float(timeout_raw)
    if timeout_seconds <= 0:
        raise ValueError(f"{prefix}: 'timeout_seconds' must be > 0")

    launch_raw = block.get("launch", [])
    if not isinstance(launch_raw, list) or not all(
        isinstance(a, str) for a in launch_raw
    ):
        raise ValueError(
            f"{prefix}: 'launch' must be a list of strings "
            "(e.g. [\"npx\", \"-y\", \"open-websearch@latest\", \"serve\"])"
        )
    launch: list[str] = list(launch_raw)

    launch_env_raw = block.get("launch_env", {})
    if not isinstance(launch_env_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in launch_env_raw.items()
    ):
        raise ValueError(f"{prefix}: 'launch_env' must be a string→string map")
    launch_env: dict[str, str] = dict(launch_env_raw)

    launch_timeout_raw = block.get("launch_ready_timeout", _DEFAULT_LAUNCH_READY_TIMEOUT)
    if not isinstance(launch_timeout_raw, (int, float)) or isinstance(
        launch_timeout_raw, bool,
    ):
        raise ValueError(f"{prefix}: 'launch_ready_timeout' must be a number")
    launch_ready_timeout = float(launch_timeout_raw)
    if launch_ready_timeout <= 0:
        raise ValueError(f"{prefix}: 'launch_ready_timeout' must be > 0")

    # `launch` only makes sense for streamable_http servers — stdio's
    # subprocess is owned by the MCP SDK's stdio_client when that transport
    # is wired (Plan E). Reject the combo loudly so operators don't write
    # a confusing config.
    if launch and transport != "streamable_http":
        raise ValueError(
            f"{prefix}: 'launch' is only valid with transport='streamable_http' "
            "(stdio servers are spawned by the MCP SDK directly)"
        )
    if launch_env and not launch:
        raise ValueError(
            f"{prefix}: 'launch_env' set without 'launch' — no subprocess to apply env to"
        )

    return MCPServerConfig(
        name=name,
        transport=transport,
        url=url if transport == "streamable_http" else None,
        command=command if transport == "stdio" else None,
        args=args,
        expose=expose,
        aliases=aliases,
        timeout_seconds=timeout_seconds,
        launch=launch,
        launch_env=launch_env,
        launch_ready_timeout=launch_ready_timeout,
    )
