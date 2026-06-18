"""Tests for startup reconciliation: orphaned `running` runs → `interrupted`.

FR-15 / Task A5: on boot, any run still marked `running` is a leftover from a
crashed/killed process — no runs are in-flight at startup. The reconciler:
1. Inserts an empty assistant placeholder for each `running` run that has a
   user message but no assistant message (pair invariant).
2. Marks all `running` runs as `interrupted`.

Uses a tmp_path DB bootstrapped from schema; mirrors the tests/db pattern.
Never touches workspace/paperhub.db.
"""

import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema, reconcile_interrupted_runs


async def _bootstrap(db_path) -> aiosqlite.Connection:
    """Open a migrated DB connection (caller must close)."""
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA foreign_keys = ON")
    await apply_schema(conn)
    return conn


async def _session_id(conn: aiosqlite.Connection) -> int:
    """Insert a chat_sessions row and return its id."""
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _run_id(conn: aiosqlite.Connection, session_id: int, status: str = "running") -> int:
    """Insert a runs row with the given status and return its id."""
    await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, ?)",
        (session_id, status),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _message(conn: aiosqlite.Connection, session_id: int, run_id: int, role: str) -> int:
    """Insert a message row and return its id."""
    await conn.execute(
        "INSERT INTO messages (session_id, run_id, role, content) VALUES (?, ?, ?, ?)",
        (session_id, run_id, role, "test content" if role == "user" else ""),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _fetch_run_status(conn: aiosqlite.Connection, run_id: int) -> str:
    async with conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def _fetch_messages(conn: aiosqlite.Connection, run_id: int) -> list[dict]:
    async with conn.execute(
        "SELECT role, content FROM messages WHERE run_id = ? ORDER BY id",
        (run_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"role": row[0], "content": row[1]} for row in rows]


# ---------------------------------------------------------------------------
# Case 1: running + lone user message → interrupted + paired assistant row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_running_lone_user_gets_assistant_placeholder_and_interrupted(tmp_path):
    """A `running` run with only a user message gets:
    - status set to 'interrupted'
    - a new assistant row with empty content inserted for that run
    """
    conn = await _bootstrap(tmp_path / "test.db")
    try:
        sid = await _session_id(conn)
        rid = await _run_id(conn, sid, status="running")
        await _message(conn, sid, rid, "user")

        await reconcile_interrupted_runs(conn)

        # Status must be interrupted.
        assert await _fetch_run_status(conn, rid) == "interrupted"

        # Messages: one user + one assistant (content empty).
        msgs = await _fetch_messages(conn, rid)
        assert len(msgs) == 2
        roles = {m["role"] for m in msgs}
        assert roles == {"user", "assistant"}
        asst = next(m for m in msgs if m["role"] == "assistant")
        assert asst["content"] == ""
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Case 2: running + already has assistant row → interrupted, no dup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_running_with_existing_assistant_no_duplicate(tmp_path):
    """A `running` run that already has an assistant row:
    - gets status set to 'interrupted'
    - does NOT get a second assistant row inserted (NOT EXISTS guard)
    """
    conn = await _bootstrap(tmp_path / "test.db")
    try:
        sid = await _session_id(conn)
        rid = await _run_id(conn, sid, status="running")
        await _message(conn, sid, rid, "user")
        await _message(conn, sid, rid, "assistant")

        await reconcile_interrupted_runs(conn)

        # Status must be interrupted.
        assert await _fetch_run_status(conn, rid) == "interrupted"

        # Messages: still exactly two (user + assistant), no dup.
        msgs = await _fetch_messages(conn, rid)
        assert len(msgs) == 2
        asst_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(asst_msgs) == 1
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Case 3: non-running run is untouched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_running_run_untouched(tmp_path):
    """Runs with status != 'running' (e.g. 'ok') are not modified at all."""
    conn = await _bootstrap(tmp_path / "test.db")
    try:
        sid = await _session_id(conn)
        rid = await _run_id(conn, sid, status="ok")
        await _message(conn, sid, rid, "user")
        await _message(conn, sid, rid, "assistant")

        await reconcile_interrupted_runs(conn)

        # Status stays 'ok'.
        assert await _fetch_run_status(conn, rid) == "ok"

        # Messages unchanged: still exactly two rows.
        msgs = await _fetch_messages(conn, rid)
        assert len(msgs) == 2
    finally:
        await conn.close()
