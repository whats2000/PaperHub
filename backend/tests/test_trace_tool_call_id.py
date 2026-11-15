"""Each trace row records the tool call it belongs to (FR-02, H5a wave 3):
parallel calls interleave their internal rows, so the activity timeline
groups them by ``tool_call_id``, not by position."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.tools import Tier, ToolContext, ToolOutcome, ToolRegistry, ToolSpec
from paperhub.db.migrate import apply_schema
from paperhub.db.tool_calls import drain_tool_calls_since
from paperhub.llm.adapter import ToolCall
from paperhub.models.domain import ToolCallRecord
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _ctx(conn: aiosqlite.Connection, tracer: Tracer) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None

    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=tracer.run_id,
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        adapter=None,  # type: ignore[arg-type]
        emit=emit, enabled_papers=[], history=[], state={},
    )


async def test_internal_rows_of_parallel_calls_name_their_call(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    gate = asyncio.Event()

    async def handler(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        async with ctx.tracer.step(agent="research", tool=f"paper_search:{args['n']}a",
                                   model=None):
            pass
        await gate.wait()
        # A task the call starts inherits the call.
        async def inner() -> None:
            async with ctx.tracer.step(agent="outbound", tool="outbound:review",
                                       model=None) as s:
                s.record_args({"n": args["n"]})
        await asyncio.create_task(inner())
        return ToolOutcome(content="ok", record={})

    reg = ToolRegistry()
    params = {"type": "object", "properties": {}, "required": []}
    reg.register(ToolSpec("work", "w", params, Tier.FREE, handler))
    ctx = await _ctx(migrated_db, fake_tracer)
    t1 = asyncio.create_task(reg.run(ctx, ToolCall(id="c1", name="work", arguments={"n": 1})))
    t2 = asyncio.create_task(reg.run(ctx, ToolCall(id="c2", name="work", arguments={"n": 2})))
    await asyncio.sleep(0.02)
    gate.set()
    await asyncio.gather(t1, t2)
    # A row outside any call has none.
    async with fake_tracer.step(agent="agent", tool="agent:step", model=None):
        pass

    rows = await drain_tool_calls_since(migrated_db, fake_tracer.run_id, -1)
    by_tool: dict[str, list[Any]] = {}
    for r in rows:
        by_tool.setdefault(r["tool"], []).append(r["tool_call_id"])
    assert sorted(by_tool["tool:work"]) == ["c1", "c2"]
    assert by_tool["paper_search:1a"] == ["c1"]
    assert by_tool["paper_search:2a"] == ["c2"]
    reviews = {(r["args_redacted_json"] or {}).get("n"): r["tool_call_id"]
               for r in rows if r["tool"] == "outbound:review"}
    assert reviews == {1: "c1", 2: "c2"}
    assert by_tool["agent:step"] == [None]
    # The trace API model carries it.
    assert ToolCallRecord(**rows[0]).tool_call_id in ("c1", "c2")


async def test_migration_adds_the_column_idempotently(tmp_path: Any) -> None:
    async with aiosqlite.connect(tmp_path / "old.db") as conn:
        await apply_schema(conn)
        await conn.execute("ALTER TABLE tool_calls DROP COLUMN tool_call_id")
        await conn.commit()
        await apply_schema(conn)
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(tool_calls)") as cur:
            cols = [r[1] for r in await cur.fetchall()]
    assert cols.count("tool_call_id") == 1
