"""Unit tests for ``paperhub.mcp.launcher`` — the shared detached-spawn helper.

The probe (``tcp_reachable`` / ``wait_until_reachable``) has its own real-socket
suite in ``test_registry_probe.py`` (imported there via the registry alias).
Here we cover ``launch_detached`` + ``terminate`` with a stubbed
``subprocess.Popen`` so we assert the detach flags + failure handling without
spawning a real process.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from paperhub.mcp import launcher


class _FakePopen:
    def __init__(self) -> None:
        self.pid = 999
        self._rc: int | None = None
        self.terminated = 0
        self.killed = 0

    def poll(self) -> int | None:
        return self._rc

    def terminate(self) -> None:
        self.terminated += 1
        self._rc = 0

    def kill(self) -> None:
        self.killed += 1
        self._rc = -9

    def wait(self, timeout: float | None = None) -> int:
        return self._rc or 0


def test_launch_detached_returns_none_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    with caplog.at_level("INFO"):
        proc = launcher.launch_detached(["nope-1234", "x"], {}, label="web")
    assert proc is None
    assert any("not on PATH" in r.message for r in caplog.records)


def test_launch_detached_spawns_detached_with_devnull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
    captured: dict[str, Any] = {}

    def _fake_popen(argv: Any, **kwargs: Any) -> Any:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakePopen()

    monkeypatch.setattr(launcher.subprocess, "Popen", _fake_popen)
    proc = launcher.launch_detached(
        ["npx", "-y", "open-websearch@latest"],
        {"PORT": "3000"},
        label="web",
    )
    assert proc is not None
    assert captured["kwargs"]["stdout"] is subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is subprocess.DEVNULL
    # env merges the override on top of os.environ.
    assert captured["kwargs"]["env"]["PORT"] == "3000"
    # Platform-appropriate detach flag is set.
    if sys.platform == "win32":
        assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert captured["kwargs"]["start_new_session"] is True


def test_launch_detached_returns_none_on_spawn_oserror(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("spawn failed")

    monkeypatch.setattr(launcher.subprocess, "Popen", _boom)
    with caplog.at_level("WARNING"):
        proc = launcher.launch_detached(["fakebin"], {}, label="web")
    assert proc is None
    assert any("spawn failed" in r.message for r in caplog.records)


def test_terminate_calls_terminate_then_returns() -> None:
    proc = _FakePopen()
    launcher.terminate(proc)  # type: ignore[arg-type]
    assert proc.terminated == 1
    assert proc.killed == 0


def test_terminate_noop_on_already_exited() -> None:
    proc = _FakePopen()
    proc._rc = 0  # already exited
    launcher.terminate(proc)  # type: ignore[arg-type]
    assert proc.terminated == 0
