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


def test_terminate_kills_the_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """terminate() must take the whole tree, not just the cmd/shell wrapper.

    The launch is ``cmd /c npx … open-websearch`` (or a POSIX shell fork), so a
    plain ``proc.terminate()`` orphans the node daemon. We assert the
    tree-killing primitive for the running platform is invoked with our PID.
    """
    proc = _FakePopen()
    proc.pid = 4242

    if sys.platform == "win32":
        calls: list[list[str]] = []

        def _fake_run(args: Any, **kwargs: Any) -> Any:
            calls.append(args)
            proc._rc = 0
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(launcher.subprocess, "run", _fake_run)
        launcher.terminate(proc)  # type: ignore[arg-type]
        assert calls == [["taskkill", "/F", "/T", "/PID", "4242"]]
    else:
        killed: list[int] = []
        monkeypatch.setattr(launcher.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(
            launcher.os, "killpg",
            lambda pgid, _sig: (killed.append(pgid), setattr(proc, "_rc", 0)),
        )
        launcher.terminate(proc)  # type: ignore[arg-type]
        assert killed == [4242]


def test_terminate_noop_on_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakePopen()
    proc._rc = 0  # already exited

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not touch an already-exited process")

    monkeypatch.setattr(launcher.subprocess, "run", _boom)
    # os.killpg is POSIX-only; patch it only where it exists.
    monkeypatch.setattr(launcher.os, "killpg", _boom, raising=False)
    launcher.terminate(proc)  # type: ignore[arg-type]
