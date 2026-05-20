"""Tests for the ``paperhub-mcp-up`` CLI (``paperhub.cli.mcp_up``).

Covers the per-server decision matrix (already-running → skip; unreachable →
launch + wait; launch fails → reported, non-fatal) and that the CLI never
exits non-zero (a down optional daemon must not fail the boot script).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from paperhub.cli import mcp_up

pytestmark = pytest.mark.asyncio


def _write_toml(tmp_path: Path, *, launch: bool) -> Path:
    launch_line = (
        'launch = ["npx", "-y", "open-websearch@latest"]\n'
        'launch_env = { PORT = "3000" }\n'
        if launch else ""
    )
    p = tmp_path / "mcp_servers.toml"
    p.write_text(
        f"""
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
{launch_line}
""",
        encoding="utf-8",
    )
    return p


async def test_ensure_one_skips_when_already_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperhub.mcp.config import load_mcp_servers

    cfg = load_mcp_servers(_write_toml(tmp_path, launch=True))[0]

    async def _reachable(host: str, port: int) -> bool:
        return True

    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not launch when already reachable")

    monkeypatch.setattr(mcp_up, "tcp_reachable", _reachable)
    monkeypatch.setattr(mcp_up, "launch_detached", _explode)

    assert await mcp_up._ensure_one(cfg) == "already-running"


async def test_ensure_one_launches_and_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperhub.mcp.config import load_mcp_servers

    cfg = load_mcp_servers(_write_toml(tmp_path, launch=True))[0]
    state = {"up": False}

    async def _reachable(host: str, port: int) -> bool:
        return state["up"]

    class _Proc:
        pid = 4242

    def _launch(launch: Any, env: Any, *, label: str = "") -> Any:
        state["up"] = True
        return _Proc()

    async def _wait(host: str, port: int, deadline_after: float) -> bool:
        return state["up"]

    monkeypatch.setattr(mcp_up, "tcp_reachable", _reachable)
    monkeypatch.setattr(mcp_up, "launch_detached", _launch)
    monkeypatch.setattr(mcp_up, "wait_until_reachable", _wait)

    assert await mcp_up._ensure_one(cfg) == "started(pid=4242)"


async def test_ensure_one_reports_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperhub.mcp.config import load_mcp_servers

    cfg = load_mcp_servers(_write_toml(tmp_path, launch=True))[0]

    async def _unreachable(host: str, port: int) -> bool:
        return False

    def _launch_fails(launch: Any, env: Any, *, label: str = "") -> Any:
        return None  # binary missing / spawn error

    monkeypatch.setattr(mcp_up, "tcp_reachable", _unreachable)
    monkeypatch.setattr(mcp_up, "launch_detached", _launch_fails)

    assert await mcp_up._ensure_one(cfg) == "launch-failed"


async def test_amain_exits_zero_with_no_launchable_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_toml(tmp_path, launch=False)
    monkeypatch.setattr(mcp_up, "resolve_config_path", lambda: path)
    monkeypatch.setattr(mcp_up, "ensure_config_seeded", lambda _p: None)
    assert await mcp_up._amain() == 0


async def test_amain_is_nonfatal_when_daemon_wont_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_toml(tmp_path, launch=True)
    monkeypatch.setattr(mcp_up, "resolve_config_path", lambda: path)
    monkeypatch.setattr(mcp_up, "ensure_config_seeded", lambda _p: None)

    async def _unreachable(host: str, port: int) -> bool:
        return False

    monkeypatch.setattr(mcp_up, "tcp_reachable", _unreachable)
    monkeypatch.setattr(mcp_up, "launch_detached", lambda *a, **k: None)

    # Even though the daemon never starts, the CLI must exit 0.
    assert await mcp_up._amain() == 0
