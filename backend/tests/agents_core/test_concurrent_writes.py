"""Concurrent tool calls in one agent step share one aiosqlite connection
(ruling R9): their writes and the tracer's row writes must not interleave
inside each other's transactions."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.action_tools import build_default_registry
from paperhub.agents.core.agent import AgentDeps, Final, run_core_agent
from paperhub.agents.core.tools import Tier, ToolContext, ToolOutcome, ToolSpec
from paperhub.db.connection import write_transaction
from paperhub.tracing.tracer import Tracer

from .test_agent_loop import FakeAdapter, _answer, _calls, _setup
from .test_read_tools import _paper

pytestmark = pytest.mark.asyncio


class FlakyWrite(Exception):
    pass


async def _flaky_writer(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """A multi-statement write that fails half-way: its first insert must
    roll back even though sibling tools and trace rows write meanwhile."""
    async with write_transaction(ctx.conn):
        await ctx.conn.execute(
            "INSERT INTO memories (scope, content) VALUES ('global', 'half-written')")
        await asyncio.sleep(0.05)
        raise FlakyWrite("boom")


async def _slow_atomic_writer(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    async with write_transaction(ctx.conn):
        for i in range(3):
            await ctx.conn.execute(
                "INSERT INTO memories (scope, content) VALUES ('global', ?)",
                (f"atomic {args['tag']} {i}",))
            await asyncio.sleep(0.01)
    return ToolOutcome(content="written", record={"tag": args["tag"]})


async def test_concurrent_tool_writes_stay_isolated(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    _, _, tracer = await _setup(migrated_db)
    pa, ids = await _paper(migrated_db, "2401.0001", {"Loss": ["L = a + b."]})
    pb, _ = await _paper(migrated_db, "2401.0002", {"Intro": ["intro"]})
    reg = build_default_registry(None)  # type: ignore[arg-type]
    obj: dict[str, Any] = {"type": "object", "properties": {}}
    reg.register(ToolSpec("flaky_writer", "f", obj, Tier.FREE, _flaky_writer))
    reg.register(ToolSpec("atomic_writer", "a",
                          {"type": "object", "properties": {"tag": {"type": "string"}}},
                          Tier.FREE, _slow_atomic_writer))
    adapter = FakeAdapter([
        _calls(
            ("add_paper", {"paper_id": f"library:{pa}"}),
            ("flaky_writer", {}),
            ("add_paper", {"paper_id": f"library:{pb}"}),
            ("remember", {"fact": "The user prefers tables.", "scope": "session"}),
            ("atomic_writer", {"tag": "x"}),
            ("read_chunks", {"ids": [ids["L = a + b."]]}),
            ("atomic_writer", {"tag": "y"}),
        ),
        _answer("done"),
    ])
    settings = SimpleNamespace(
        agent_model="m", agent_max_steps=12, agent_max_turn_tokens=120000, agent_keep_turns=12, papers_cache_dir=tmp_path, unpaywall_email=None,
        memory_conflict_model="m")
    deps = AgentDeps(adapter=adapter, tracer=tracer, conn=migrated_db,  # type: ignore[arg-type]
                     settings=settings, registry=reg)  # type: ignore[arg-type]
    events = [e async for e in run_core_agent(
        {"run_id": 1, "session_id": 1, "user_message": "q", "history": []}, deps)]
    assert isinstance(events[-1], Final)

    tool_msgs = [m for m in adapter.calls[1]["messages"] if m["role"] == "tool"]
    errors = [m["content"] for m in tool_msgs if m["content"].startswith("Error")]
    # Only the deliberately failing writer errs, and never with a
    # transaction/lock error.
    assert len(errors) == 1 and "FlakyWrite: boom" in errors[0]

    async with migrated_db.execute(
        "SELECT paper_content_id FROM papers WHERE session_id = 1 ORDER BY 1") as cur:
        assert [r[0] for r in await cur.fetchall()] == sorted([pa, pb])
    async with migrated_db.execute("SELECT content FROM memories ORDER BY id") as cur:
        contents = [r[0] for r in await cur.fetchall()]
    assert "half-written" not in contents  # the failed write rolled back
    assert "The user prefers tables." in contents
    for tag in ("x", "y"):
        assert [c for c in contents if c.startswith(f"atomic {tag}")] == [
            f"atomic {tag} {i}" for i in range(3)]
    async with migrated_db.execute(
        "SELECT tool FROM tool_calls WHERE run_id = 1 ORDER BY step_index") as cur:
        tools = [r[0] for r in await cur.fetchall()]
    for name in ("add_paper", "flaky_writer", "remember", "atomic_writer",
                 "read_chunks"):
        assert f"tool:{name}" in tools
    assert tools.count("tool:add_paper") == 2
    assert tools.count("tool:atomic_writer") == 2
    assert tools.count("agent:step") == 2


async def test_traced_step_inside_write_transaction_does_not_deadlock(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    async def body() -> None:
        async with write_transaction(migrated_db):
            await migrated_db.execute(
                "INSERT INTO memories (scope, content) VALUES ('global', 'in tx')")
            async with fake_tracer.step(agent="a", tool="t:inner", model=None) as st:
                st.record_result({"ok": True})

    await asyncio.wait_for(body(), timeout=3)
    async with migrated_db.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE tool = 't:inner'") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1
    async with migrated_db.execute(
        "SELECT COUNT(*) FROM memories WHERE content = 'in tx'") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1


async def test_traced_step_inside_failed_write_transaction_rolls_back_with_it(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """A trace row written inside a write transaction is part of it: it
    must not commit the caller's half-written transaction early."""
    with pytest.raises(FlakyWrite):
        async with write_transaction(migrated_db):
            await migrated_db.execute(
                "INSERT INTO memories (scope, content) VALUES ('global', 'doomed')")
            async with fake_tracer.step(agent="a", tool="t:inner", model=None):
                pass
            raise FlakyWrite("x")
    async with migrated_db.execute(
        "SELECT COUNT(*) FROM memories WHERE content = 'doomed'") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 0


async def test_failed_raw_write_block_rolls_back(
    migrated_db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_memory_with_supersede: a failure between its INSERT and commit
    leaves no row and no open transaction behind."""
    from paperhub.agents import memory_tools

    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    real_execute = migrated_db.execute
    calls = {"n": 0}

    def failing_execute(sql: str, *a: Any, **k: Any) -> Any:
        if sql.startswith("SELECT last_insert_rowid()"):
            calls["n"] += 1
            raise RuntimeError("db hiccup")
        return real_execute(sql, *a, **k)

    monkeypatch.setattr(migrated_db, "execute", failing_execute)
    with pytest.raises(RuntimeError, match="db hiccup"):
        await memory_tools.add_memory_with_supersede(
            migrated_db, session_id=1, content="Prefers tables.", scope="session",
            adapter=None, model="m")
    monkeypatch.setattr(migrated_db, "execute", real_execute)
    assert calls["n"] == 1
    assert not migrated_db.in_transaction
    async with migrated_db.execute("SELECT COUNT(*) FROM memories") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 0
