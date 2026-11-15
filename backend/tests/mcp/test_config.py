"""Tests for `paperhub.mcp.config` loader + validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from paperhub.mcp.config import MCPServerConfig, load_mcp_servers


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "mcp_servers.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_streamable_http_block(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search", "fetchWebContent"]
aliases = { "fetchWebContent" = "fetch" }
timeout_seconds = 8.0
""",
    )
    servers = load_mcp_servers(path)
    assert len(servers) == 1
    cfg = servers[0]
    assert cfg.name == "web"
    assert cfg.transport == "streamable_http"
    assert cfg.url == "http://localhost:3000/mcp"
    assert cfg.expose == ["search", "fetchWebContent"]
    assert cfg.aliases == {"fetchWebContent": "fetch"}
    assert cfg.timeout_seconds == pytest.approx(8.0)
    assert cfg.command is None
    assert cfg.args == []


def test_load_multiple_servers(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]

[[server]]
name = "sql"
transport = "streamable_http"
url = "http://localhost:3100/mcp"
expose = ["query"]
""",
    )
    servers = load_mcp_servers(path)
    assert [s.name for s in servers] == ["web", "sql"]


def test_load_stdio_block(tmp_path: Path) -> None:
    """Stdio config schema is accepted (dispatch is not implemented yet)."""
    path = _write(
        tmp_path,
        """
[[server]]
name = "fs"
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
expose = ["read_file"]
""",
    )
    servers = load_mcp_servers(path)
    assert len(servers) == 1
    cfg = servers[0]
    assert cfg.transport == "stdio"
    assert cfg.command == "npx"
    assert cfg.args == ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    assert cfg.url is None


def test_default_timeout_when_omitted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
""",
    )
    cfg = load_mcp_servers(path)[0]
    assert cfg.timeout_seconds > 0  # default applied


def test_missing_name_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
""",
    )
    with pytest.raises(ValueError, match=r"server\[0\].*name"):
        load_mcp_servers(path)


def test_missing_transport_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
url = "http://localhost:3000/mcp"
expose = ["search"]
""",
    )
    with pytest.raises(ValueError, match=r"transport"):
        load_mcp_servers(path)


def test_unknown_transport_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "websocket"
url = "ws://localhost:3000/mcp"
expose = ["search"]
""",
    )
    with pytest.raises(ValueError, match=r"transport"):
        load_mcp_servers(path)


def test_streamable_http_requires_url(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
expose = ["search"]
""",
    )
    with pytest.raises(ValueError, match=r"url"):
        load_mcp_servers(path)


def test_stdio_requires_command(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "fs"
transport = "stdio"
expose = ["read_file"]
""",
    )
    with pytest.raises(ValueError, match=r"command"):
        load_mcp_servers(path)


def test_alias_referencing_unknown_tool_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
aliases = { "fetchWebContent" = "fetch" }
""",
    )
    with pytest.raises(ValueError, match=r"alias"):
        load_mcp_servers(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_mcp_servers(tmp_path / "nope.toml")


def test_empty_file_returns_empty_list(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    assert load_mcp_servers(path) == []


def test_dataclass_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    cfg = MCPServerConfig(
        name="web",
        transport="streamable_http",
        url="http://x",
        expose=["search"],
    )
    with pytest.raises(FrozenInstanceError):
        cfg.name = "other"  # type: ignore[misc]


def test_load_launch_fields(tmp_path: Path) -> None:
    """The v2.6 autostart fields parse and round-trip via MCPServerConfig."""
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
launch = ["npx", "-y", "open-websearch@latest", "serve"]
launch_env = { PORT = "3000" }
launch_ready_timeout = 20.0
""",
    )
    cfg = load_mcp_servers(path)[0]
    assert cfg.launch == ["npx", "-y", "open-websearch@latest", "serve"]
    assert cfg.launch_env == {"PORT": "3000"}
    assert cfg.launch_ready_timeout == pytest.approx(20.0)
    assert cfg.has_launch is True


def test_launch_default_is_empty_list(tmp_path: Path) -> None:
    """Configs that don't set `launch` have `has_launch=False` and no spawn."""
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
""",
    )
    cfg = load_mcp_servers(path)[0]
    assert cfg.launch == []
    assert cfg.has_launch is False


def test_launch_rejected_for_stdio_transport(tmp_path: Path) -> None:
    """stdio servers can't use `launch` — the MCP SDK owns their lifecycle."""
    path = _write(
        tmp_path,
        """
[[server]]
name = "sql"
transport = "stdio"
command = "uv"
expose = ["query"]
launch = ["uv", "run", "paperhub-sqlite-mcp"]
""",
    )
    with pytest.raises(ValueError, match="'launch' is only valid"):
        load_mcp_servers(path)


def test_launch_env_without_launch_rejected(tmp_path: Path) -> None:
    """`launch_env` orphaned without `launch` is a config error."""
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
launch_env = { PORT = "3000" }
""",
    )
    with pytest.raises(ValueError, match="launch_env"):
        load_mcp_servers(path)


def test_launch_ready_timeout_must_be_positive(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
launch = ["sleep", "1"]
launch_ready_timeout = 0
""",
    )
    with pytest.raises(ValueError, match="launch_ready_timeout"):
        load_mcp_servers(path)


def test_launch_rejects_non_string_elements(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
launch = ["npx", 2]
""",
    )
    with pytest.raises(ValueError, match="'launch'"):
        load_mcp_servers(path)


def test_public_web_flag_defaults_by_name_and_can_be_explicit(tmp_path: Path) -> None:
    p = tmp_path / "s.toml"
    p.write_text(
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://h:1/mcp"
expose = ["search"]

[[server]]
name = "search2"
transport = "streamable_http"
url = "http://h:2/mcp"
expose = ["search"]
public_web = true

[[server]]
name = "web2"
transport = "streamable_http"
url = "http://h:3/mcp"
expose = ["search"]
public_web = false
""",
        encoding="utf-8",
    )
    cfgs = {c.name: c.public_web for c in load_mcp_servers(p)}
    assert cfgs == {"web": True, "search2": True, "web2": False}
