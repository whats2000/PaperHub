"""The tracer never loses a step to a PK collision (H4 Task 8 fix round)."""
from __future__ import annotations

import aiosqlite
import pytest

from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _indexes(conn: aiosqlite.Connection) -> list[int]:
    async with conn.execute(
        "SELECT step_index FROM tool_calls WHERE run_id = 1 ORDER BY step_index",
    ) as cur:
        return [int(r[0]) for r in await cur.fetchall()]


async def test_append_only_tracer_takes_next_free_index(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    async with fake_tracer.step(agent="a", tool="t0", model=None):
        pass
    late = Tracer(migrated_db, run_id=1, branch="", append_only=True)
    async with late.step(agent="a", tool="late", model=None):
        # a newer row lands while the step is open
        await migrated_db.execute(
            "INSERT INTO tool_calls (run_id, branch, step_index, agent, tool, status, latency_ms) "
            "VALUES (1, '', 1, 'a', 'x', 'ok', 0)")
        await migrated_db.commit()
    assert await _indexes(migrated_db) == [0, 1, 2]


async def test_run_tracer_survives_index_taken_by_a_confirmed_step(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    async with fake_tracer.step(agent="a", tool="t0", model=None):
        pass
    late = Tracer(migrated_db, run_id=1, branch="", append_only=True)
    async with late.step(agent="a", tool="confirmed", model=None):
        pass  # takes index 1 while the run is still streaming
    async with fake_tracer.step(agent="a", tool="t1", model=None):
        pass  # the run's own next index is also 1
    assert await _indexes(migrated_db) == [0, 1, 2]
