"""Tests for `paperhub.mcp.registry.MCPRegistry`."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from paperhub.mcp.errors import MCPToolError, MCPUnavailableError
from paperhub.mcp.registry import MCPRegistry


def _write_two_server_toml(tmp_path: Path) -> Path:
    p = tmp_path / "mcp_servers.toml"
    p.write_text(
        """
[[server]]
name = "web"
transport = "streamable_http"
url = "http://localhost:3000/mcp"
expose = ["search"]
timeout_seconds = 5.0

[[server]]
name = "dead"
transport = "streamable_http"
url = "http://localhost:9999/mcp"
expose = ["ping"]
timeout_seconds = 5.0
""",
        encoding="utf-8",
    )
    return p


class _FakeClient:
    """Stand-in for MCPClient with deterministic connect/list/call behaviour."""

    def __init__(
        self,
        name: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        connect_exc: BaseException | None = None,
        connect_fails_first_n: int = 0,
        call_result: Any = None,
        call_exc: BaseException | None = None,
    ) -> None:
        self._name = name
        self._tools = tools or []
        self._connect_exc = connect_exc
        # When > 0, the first N connect() calls raise ``connect_exc``;
        # subsequent calls succeed. Used to exercise the registry's
        # retry-after-cooldown path.
        self._connect_fails_remaining = connect_fails_first_n
        self._has_recovery_scenario = connect_fails_first_n > 0
        self._call_result = call_result
        self._call_exc = call_exc
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.list_tools_calls = 0
        self.call_tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.connected = False

    @property
    def name(self) -> str:
        return self._name

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connected:
            return
        # Recovery scenario: first N attempts fail, rest succeed.
        if self._has_recovery_scenario:
            if self._connect_fails_remaining > 0:
                self._connect_fails_remaining -= 1
                if self._connect_exc is not None:
                    raise self._connect_exc
                return
            self.connected = True
            return
        # Always-fail scenario.
        if self._connect_exc is not None:
            raise self._connect_exc
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        self.list_tools_calls += 1
        return list(self._tools)

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.call_tool_calls.append((name, args))
        if self._call_exc is not None:
            raise self._call_exc
        return self._call_result


def _patch_clients(
    monkeypatch: pytest.MonkeyPatch,
    clients_by_name: dict[str, _FakeClient],
) -> None:
    """Patch MCPClient inside the registry module to return fakes by config name."""
    from paperhub.mcp import registry as registry_mod

    def _ctor(config: Any) -> _FakeClient:
        try:
            return clients_by_name[config.name]
        except KeyError as exc:
            raise AssertionError(
                f"test setup missing fake for server {config.name!r}"
            ) from exc

    monkeypatch.setattr(registry_mod, "MCPClient", _ctor)


# --- tests -------------------------------------------------------------------


async def test_startup_does_not_connect_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient(
        "web",
        tools=[
            {
                "type": "function",
                "function": {"name": "web.search", "description": "", "parameters": {}},
            }
        ],
    )
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("daemon down"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    assert web.connect_calls == 0
    assert dead.connect_calls == 0


async def test_aggregate_tool_schemas_triggers_lazy_connect_and_skips_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    web_tools = [
        {
            "type": "function",
            "function": {
                "name": "web.search",
                "description": "",
                "parameters": {"type": "object"},
            },
        }
    ]
    web = _FakeClient("web", tools=web_tools)
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("daemon down"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    with caplog.at_level(logging.WARNING, logger="paperhub.mcp.registry"):
        schemas = await reg.aggregate_tool_schemas()

    assert web.connect_calls == 1
    assert dead.connect_calls == 1
    assert [s["function"]["name"] for s in schemas] == ["web.search"]
    assert any("dead" in r.getMessage() for r in caplog.records)


async def test_aggregate_tool_schemas_retries_failed_server_after_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: when a daemon is slow to come up (npx download +
    Playwright init can take 30-60s), the first lazy-connect fails
    and the registry would PERMANENTLY exclude the server from the
    palette for the whole backend lifecycle — operators had to
    restart. Now after a cooldown the registry retries, so the
    daemon enters the palette without operator intervention."""
    slow_tools = [
        {
            "type": "function",
            "function": {
                "name": "web.search", "description": "",
                "parameters": {"type": "object"},
            },
        },
    ]
    # web fails the first connect attempt, succeeds afterwards.
    web = _FakeClient(
        "web", tools=slow_tools,
        connect_exc=MCPUnavailableError("daemon not ready"),
        connect_fails_first_n=1,
    )
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("permanently down"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    # Aggressive cooldown so the test doesn't sleep 30s.
    monkeypatch.setattr(reg, "_RETRY_AFTER_SECONDS", 0.0)
    await reg.startup(_write_two_server_toml(tmp_path))

    # First call: web fails connect → not in palette.
    schemas_1 = await reg.aggregate_tool_schemas()
    assert "web.search" not in [s["function"]["name"] for s in schemas_1]
    assert web.connect_calls == 1

    # Second call after cooldown: web is retried and now succeeds.
    schemas_2 = await reg.aggregate_tool_schemas()
    assert "web.search" in [s["function"]["name"] for s in schemas_2]
    assert web.connect_calls == 2

    # 'dead' was retried too (cooldown elapsed) but stays failed —
    # both eligible servers are probed each cooldown window.
    assert dead.connect_calls == 2


async def test_aggregate_tool_schemas_caches_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient(
        "web",
        tools=[
            {
                "type": "function",
                "function": {"name": "web.search", "description": "", "parameters": {}},
            }
        ],
    )
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    await reg.aggregate_tool_schemas()
    await reg.aggregate_tool_schemas()
    await reg.aggregate_tool_schemas()

    # Connect attempted once per server across the lifecycle — failed
    # server is not retried on every palette query.
    assert web.connect_calls == 1
    assert dead.connect_calls == 1
    assert web.list_tools_calls == 1


async def test_call_routes_to_named_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient(
        "web",
        tools=[
            {
                "type": "function",
                "function": {"name": "web.search", "description": "", "parameters": {}},
            }
        ],
        call_result={"hits": [{"title": "x"}]},
    )
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    result = await reg.call("web.search", {"q": "diffusion"})

    assert result == {"hits": [{"title": "x"}]}
    assert web.call_tool_calls == [("search", {"q": "diffusion"})]


async def test_call_unknown_namespace_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient("web", tools=[])
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    with pytest.raises(MCPUnavailableError, match=r"no server named 'unknown'"):
        await reg.call("unknown.tool", {})


async def test_call_missing_dot_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient("web", tools=[])
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    with pytest.raises(ValueError, match=r"namespaced"):
        await reg.call("notnamespaced", {})


async def test_call_unreachable_server_raises_and_invalidates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient("web", tools=[])
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    # Prime the cache.
    await reg.aggregate_tool_schemas()
    assert reg._aggregated_schemas is not None  # type: ignore[attr-defined]

    with pytest.raises(MCPUnavailableError):
        await reg.call("dead.ping", {})

    # Cache invalidated so the next palette query gets re-asked.
    assert reg._aggregated_schemas is None  # type: ignore[attr-defined]


async def test_call_propagates_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient(
        "web", tools=[], call_exc=MCPToolError("upstream said no")
    )
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    # Prime the cache first so the post-condition assertion is meaningful.
    await reg.aggregate_tool_schemas()
    assert reg._aggregated_schemas is not None  # type: ignore[attr-defined]

    with pytest.raises(MCPToolError, match=r"upstream said no"):
        await reg.call("web.search", {})

    # MCPToolError is an upstream/tool-level failure — cache stays primed.
    # (Contrast with MCPUnavailableError, which invalidates the cache.)
    assert reg._aggregated_schemas is not None  # type: ignore[attr-defined]


async def test_has_tool_after_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient(
        "web",
        tools=[
            {
                "type": "function",
                "function": {"name": "web.search", "description": "", "parameters": {}},
            }
        ],
    )
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))

    assert await reg.has_tool("web.search") is True
    assert await reg.has_tool("dead.ping") is False
    assert await reg.has_tool("nope.nope") is False


async def test_shutdown_disconnects_connected_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _FakeClient(
        "web",
        tools=[
            {
                "type": "function",
                "function": {"name": "web.search", "description": "", "parameters": {}},
            }
        ],
    )
    dead = _FakeClient("dead", connect_exc=MCPUnavailableError("nope"))
    _patch_clients(monkeypatch, {"web": web, "dead": dead})

    reg = MCPRegistry()
    await reg.startup(_write_two_server_toml(tmp_path))
    await reg.aggregate_tool_schemas()
    await reg.shutdown()

    # Shutdown calls disconnect on every client (idempotent on the client side).
    assert web.disconnect_calls == 1
    assert dead.disconnect_calls == 1


async def test_startup_missing_toml_yields_empty_registry(tmp_path: Path) -> None:
    """Fresh-clone: missing mcp_servers.toml is non-fatal."""
    reg = MCPRegistry()
    await reg.startup(tmp_path / "does_not_exist.toml")
    schemas = await reg.aggregate_tool_schemas()
    assert schemas == []
    assert await reg.has_tool("anything.at.all") is False
    # shutdown on empty registry is safe.
    await reg.shutdown()


# --- launch / autostart -------------------------------------------------------


def _write_launch_toml(tmp_path: Path, *, launch_line: str = "") -> Path:
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


class _FakePopen:
    """subprocess.Popen stand-in: tracks terminate/kill/wait calls."""

    def __init__(self) -> None:
        self.pid = 4242
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = 0  # graceful exit

    def kill(self) -> None:
        self.kill_calls += 1
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode or 0


async def test_startup_skips_launch_when_url_already_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The registry's TCP probe shortcuts the spawn when a daemon already serves."""
    web = _FakeClient("web")
    _patch_clients(monkeypatch, {"web": web})

    from paperhub.mcp import registry as registry_mod

    async def _stub_reachable(host: str, port: int) -> bool:
        return True  # daemon already up

    spawn_calls: list[tuple[Any, ...]] = []

    def _explosive_spawn(*args: Any, **kwargs: Any) -> Any:
        spawn_calls.append(args)
        raise AssertionError("should not spawn when daemon is already reachable")

    monkeypatch.setattr(registry_mod, "tcp_reachable", _stub_reachable)
    monkeypatch.setattr(registry_mod, "launch_detached", _explosive_spawn)

    reg = MCPRegistry()
    with caplog.at_level(logging.INFO):
        await reg.startup(
            _write_launch_toml(
                tmp_path,
                launch_line='launch = ["sleep", "60"]',
            ),
        )
    assert spawn_calls == []
    assert any("already reachable" in r.message for r in caplog.records)


async def test_startup_spawns_subprocess_and_leaks_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreachable URL + launch set → spawn (detached Popen); shutdown LEAKS it.

    Detach-and-leak: the daemon must OUTLIVE the worker so `uvicorn --reload`
    doesn't kill + re-pay the ~25s npx cold start every edit. So shutdown must
    NOT terminate the launched daemon (regression guard).
    """
    web = _FakeClient("web")
    _patch_clients(monkeypatch, {"web": web})

    from paperhub.mcp import registry as registry_mod

    # Simulate: probe says NOT reachable initially; then reachable once the
    # subprocess "started" (we toggle the flag after the spawn).
    state = {"daemon_up": False}

    async def _stub_reachable(host: str, port: int) -> bool:
        return state["daemon_up"]

    fake_proc = _FakePopen()

    def _stub_launch(launch: Any, launch_env: Any, *, label: str = "") -> Any:
        state["daemon_up"] = True  # spawn "succeeded"
        return fake_proc

    # Stub the post-launch readiness wait too — the real wait_until_reachable
    # calls the launcher's own tcp_reachable (not the registry-level name we
    # patch), which would otherwise hit a real socket on :3000.
    async def _stub_wait(host: str, port: int, deadline_after: float) -> bool:
        return state["daemon_up"]

    monkeypatch.setattr(registry_mod, "tcp_reachable", _stub_reachable)
    monkeypatch.setattr(registry_mod, "launch_detached", _stub_launch)
    monkeypatch.setattr(registry_mod, "wait_until_reachable", _stub_wait)

    reg = MCPRegistry()
    await reg.startup(
        _write_launch_toml(
            tmp_path,
            launch_line='launch = ["fakebin"]\nlaunch_ready_timeout = 5.0',
        ),
    )

    # The fake process is tracked; shutdown does NOT terminate it (detach-and-leak).
    assert "web" in reg._launched  # noqa: SLF001 — inspecting private state
    assert reg._launched["web"] is fake_proc  # noqa: SLF001
    await reg.shutdown()
    assert fake_proc.terminate_calls == 0
    assert "web" in reg._launched  # noqa: SLF001 — still tracked, still running


async def test_startup_skips_launch_when_binary_not_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """`launch_detached` returning None (binary missing) → no tracked proc, no error."""
    web = _FakeClient("web")
    _patch_clients(monkeypatch, {"web": web})

    from paperhub.mcp import registry as registry_mod

    async def _stub_unreachable(host: str, port: int) -> bool:
        return False

    def _missing_binary(launch: Any, launch_env: Any, *, label: str = "") -> Any:
        # Mirror launcher.launch_detached's PATH-miss path: log + return None.
        logging.getLogger("paperhub.mcp.launcher").info(
            "%s: launch binary %r not on PATH", label, launch[0],
        )
        return None

    monkeypatch.setattr(registry_mod, "tcp_reachable", _stub_unreachable)
    monkeypatch.setattr(registry_mod, "launch_detached", _missing_binary)

    reg = MCPRegistry()
    with caplog.at_level(logging.INFO):
        await reg.startup(
            _write_launch_toml(
                tmp_path,
                launch_line='launch = ["nonexistent-binary-1234"]',
            ),
        )
    assert reg._launched == {}  # noqa: SLF001
    assert any("not on PATH" in r.message for r in caplog.records)


