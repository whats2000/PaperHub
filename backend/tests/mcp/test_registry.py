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
        call_result: Any = None,
        call_exc: BaseException | None = None,
    ) -> None:
        self._name = name
        self._tools = tools or []
        self._connect_exc = connect_exc
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


