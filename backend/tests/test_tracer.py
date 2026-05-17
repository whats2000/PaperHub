import asyncio

import aiosqlite
import pytest

from paperhub.tracing.tracer import Tracer


async def _new_run(db: aiosqlite.Connection) -> int:
    await db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await db.commit()
    async with db.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_ok_call_writes_one_row(migrated_db: aiosqlite.Connection) -> None:
    run_id = await _new_run(migrated_db)
    tracer = Tracer(migrated_db, run_id=run_id, branch="")
    async with tracer.step(agent="router", tool="classify", model="x") as step:
        step.record_args({"prompt": "hi"})
        step.record_result({"intent": "chitchat"})
        step.record_tokens(token_in=5, token_out=2)
    async with migrated_db.execute(
        "SELECT agent, tool, status, token_in, token_out FROM tool_calls"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("router", "classify", "ok", 5, 2)]


async def test_redacts_args(migrated_db: aiosqlite.Connection) -> None:
    run_id = await _new_run(migrated_db)
    tracer = Tracer(migrated_db, run_id=run_id, branch="")
    async with tracer.step(agent="router", tool="classify", model=None) as step:
        step.record_args({"api_key": "sk-ant-api03-SECRET12345"})
    async with migrated_db.execute(
        "SELECT args_redacted_json FROM tool_calls"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "SECRET12345" not in row[0]
    assert "<redacted:anthropic>" in row[0]


async def test_exception_marks_error(migrated_db: aiosqlite.Connection) -> None:
    run_id = await _new_run(migrated_db)
    tracer = Tracer(migrated_db, run_id=run_id, branch="")
    with pytest.raises(RuntimeError):
        async with tracer.step(agent="router", tool="classify", model=None):
            raise RuntimeError("boom")
    async with migrated_db.execute(
        "SELECT status, error FROM tool_calls"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("error", "boom")]


async def test_cancellation_finalises_row(migrated_db: aiosqlite.Connection) -> None:
    run_id = await _new_run(migrated_db)
    tracer = Tracer(migrated_db, run_id=run_id, branch="")

    async def work() -> None:
        async with tracer.step(agent="router", tool="classify", model=None):
            await asyncio.sleep(10)

    task = asyncio.create_task(work())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with migrated_db.execute("SELECT status FROM tool_calls") as cur:
        rows = await cur.fetchall()
    assert rows == [("cancelled",)] or rows == [("error",)]
    # Either status is acceptable per NFR-04 as long as a row exists.


async def test_step_index_monotonic(migrated_db: aiosqlite.Connection) -> None:
    run_id = await _new_run(migrated_db)
    tracer = Tracer(migrated_db, run_id=run_id, branch="")
    for _ in range(3):
        async with tracer.step(agent="router", tool="classify", model=None):
            pass
    async with migrated_db.execute(
        "SELECT step_index FROM tool_calls ORDER BY step_index"
    ) as cur:
        rows = await cur.fetchall()
    assert [r[0] for r in rows] == [0, 1, 2]


async def test_branch_isolation(migrated_db: aiosqlite.Connection) -> None:
    run_id = await _new_run(migrated_db)
    ta = Tracer(migrated_db, run_id=run_id, branch="A")
    tb = Tracer(migrated_db, run_id=run_id, branch="B")
    async with ta.step(agent="router", tool="classify", model=None):
        pass
    async with tb.step(agent="router", tool="classify", model=None):
        pass
    async with migrated_db.execute(
        "SELECT branch, step_index FROM tool_calls ORDER BY branch"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("A", 0), ("B", 0)]
