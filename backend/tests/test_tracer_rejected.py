import aiosqlite
import pytest

from paperhub.tracing.tracer import Tracer


@pytest.mark.asyncio
async def test_mark_rejected_writes_rejected_status(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")

    async with tracer.step(agent="sql", tool="sql.query", model=None) as step:
        step.record_args({"sql": "DROP TABLE papers"})
        step.mark_rejected("verb 'DROP' not in {SELECT, WITH}")

    async with migrated_db.execute(
        "SELECT status, error FROM tool_calls WHERE run_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "rejected"
    assert "DROP" in row[1]
