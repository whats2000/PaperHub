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
    # Nothing started → no ports file (cleanup has nothing to do).
    assert not mcp_up._ports_file().exists()


async def test_amain_writes_started_ports_for_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daemons we START are recorded so the boot script can tree-kill them."""
    path = _write_toml(tmp_path, launch=True)
    monkeypatch.setattr(mcp_up, "resolve_config_path", lambda: path)
    monkeypatch.setattr(mcp_up, "ensure_config_seeded", lambda _p: None)
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

    assert await mcp_up._amain() == 0
    # The web block's url is :3000 — that port must be recorded.
    assert mcp_up._ports_file().read_text(encoding="utf-8").split() == ["3000"]


async def test_amain_clears_stale_ports_file_when_already_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon already running (we didn't start it) must not be recorded;
    a stale ports file is cleared so the boot script won't kill a daemon we
    don't own."""
    path = _write_toml(tmp_path, launch=True)
    monkeypatch.setattr(mcp_up, "resolve_config_path", lambda: path)
    monkeypatch.setattr(mcp_up, "ensure_config_seeded", lambda _p: None)
    # Pre-existing stale file from a prior run.
    mcp_up._ports_file().write_text("9999\n", encoding="utf-8")

    async def _reachable(host: str, port: int) -> bool:
        return True

    monkeypatch.setattr(mcp_up, "tcp_reachable", _reachable)
    monkeypatch.setattr(
        mcp_up, "launch_detached",
        lambda *a, **k: pytest.fail("must not launch when already running"),
    )

    assert await mcp_up._amain() == 0
    assert not mcp_up._ports_file().exists()


async def test_amain_skips_public_web_server_when_search_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_toml(tmp_path, launch=True)
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(path))
    monkeypatch.setenv("PAPERHUB_PUBLIC_WEB_SEARCH", "0")
    monkeypatch.setenv("PAPERHUB_MCP_PORTS_FILE", str(tmp_path / "ports"))
    seen: list[str] = []

    async def _ensure(cfg: Any) -> str:
        seen.append(cfg.name)
        return "already-running"

    monkeypatch.setattr(mcp_up, "_ensure_one", _ensure)
    assert await mcp_up._amain() == 0
    assert seen == []
    monkeypatch.setenv("PAPERHUB_PUBLIC_WEB_SEARCH", "1")
    assert await mcp_up._amain() == 0
    assert seen == ["web"]


def _settings_db(tmp_path: Path, rows: dict[str, str]) -> None:
    import sqlite3

    with sqlite3.connect(tmp_path / "paperhub.db") as c:
        c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        c.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", list(rows.items()))


async def _launched_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("PAPERHUB_MCP_CONFIG", str(_write_toml(tmp_path, launch=True)))
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    seen: list[str] = []

    async def _ensure(cfg: Any) -> str:
        seen.append(cfg.name)
        return "already-running"

    monkeypatch.setattr(mcp_up, "_ensure_one", _ensure)
    assert await mcp_up._amain() == 0
    return seen


async def test_amain_honours_public_search_switched_on_in_settings_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")
    monkeypatch.delenv("PAPERHUB_PUBLIC_WEB_SEARCH", raising=False)
    monkeypatch.delenv("PAPERHUB_LOCKED_SETTINGS", raising=False)
    _settings_db(tmp_path, {"PAPERHUB_PUBLIC_WEB_SEARCH": "1"})
    assert await _launched_names(tmp_path, monkeypatch) == ["web"]


async def test_amain_ignores_locked_settings_db_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")
    monkeypatch.delenv("PAPERHUB_PUBLIC_WEB_SEARCH", raising=False)
    monkeypatch.setenv("PAPERHUB_LOCKED_SETTINGS", "PAPERHUB_PUBLIC_WEB_SEARCH")
    _settings_db(tmp_path, {"PAPERHUB_PUBLIC_WEB_SEARCH": "1"})
    assert await _launched_names(tmp_path, monkeypatch) == []
