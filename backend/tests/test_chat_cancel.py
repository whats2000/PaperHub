"""Tests for POST /chat/cancel — the only cancel path (Task A3, FR-15).

Four cases:
1. Cancels a registered asyncio task.
2. Deletes messages + sets status='cancelled' for a running run.
3. Race guard: an already-ok run keeps its messages + stays ok.
4. No-handle running run: DB cleanup still fires without error.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import aiosqlite
import pytest

from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _bootstrap_schema(tmp_path: Any) -> None:
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)


# ---------------------------------------------------------------------------
# Autouse fixture: clear the module-level broker between every test.
# The broker is a process-level singleton; each test uses a fresh DB whose
# run_ids restart at 1, so a stale handle would shadow the new one.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_broker() -> Iterator[None]:
    import paperhub.api.chat as chat_module

    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()
    yield
    chat_module.broker._handles.clear()
    chat_module._live_tasks.clear()


# ---------------------------------------------------------------------------
# Test 1 — Cancels a registered asyncio.Task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_registered_task(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_run() must cancel the live asyncio.Task in the handle."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)

    import paperhub.api.chat as chat_module
    from paperhub.api.chat import CancelRequest, cancel_run
    from paperhub.api.run_broker import RunBroker

    # Register a handle with a long-running task (stand-in for a live LLM call).
    broker: RunBroker = chat_module.broker

    # We need a session + run row so the DB update doesn't raise FK errors.
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.commit()
        await conn.execute(
            "INSERT INTO runs (session_id, status) VALUES (1, 'running')"
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        run_id = int(row[0])

    # Create a task that would run for 30 seconds; register it on the handle.
    task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(30))
    handle = broker.register(run_id)
    handle.task = task
    chat_module._live_tasks.add(task)
    task.add_done_callback(chat_module._live_tasks.discard)

    # Call the cancel endpoint.
    await cancel_run(CancelRequest(run_id=run_id))

    # Yield a tick so the cancellation propagates to the task.
    await asyncio.sleep(0)

    assert task.cancelled(), "cancel_run must cancel the live asyncio.Task"


# ---------------------------------------------------------------------------
# Test 2 — Deletes messages + sets status='cancelled' for a running run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_deletes_messages_and_sets_cancelled(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_run() must retract the user message and mark the run cancelled."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)

    from paperhub.api.chat import CancelRequest, cancel_run

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.commit()
        await conn.execute(
            "INSERT INTO runs (session_id, status) VALUES (1, 'running')"
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        run_id = int(row[0])

        # Seed a user message tied to this run.
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'hello', ?)",
            (run_id,),
        )
        await conn.commit()

    await cancel_run(CancelRequest(run_id=run_id))

    async with aiosqlite.connect(settings.db_path) as conn:
        # The message must be gone.
        async with conn.execute(
            "SELECT id FROM messages WHERE run_id = ?", (run_id,)
        ) as cur:
            msg_row = await cur.fetchone()
        assert msg_row is None, "cancel must delete the message row for a running run"

        # The run status must be 'cancelled'.
        async with conn.execute(
            "SELECT status FROM runs WHERE id = ?", (run_id,)
        ) as cur:
            run_row = await cur.fetchone()
        assert run_row is not None
        assert run_row[0] == "cancelled", (
            f"expected status='cancelled', got {run_row[0]!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Race guard: an already-ok run is left untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_race_guard_leaves_ok_run_intact(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_run() must NOT nuke messages or change status on an already-ok run."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)

    from paperhub.api.chat import CancelRequest, cancel_run

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.commit()
        # Seed a run that's already completed (status='ok').
        await conn.execute(
            "INSERT INTO runs (session_id, status) VALUES (1, 'ok')"
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        run_id = int(row[0])

        # Seed both user and assistant messages tied to this run.
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'hello', ?)",
            (run_id,),
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'assistant', 'world', ?)",
            (run_id,),
        )
        await conn.commit()

    # Cancel fires on an already-finished run — the race guard must block both writes.
    await cancel_run(CancelRequest(run_id=run_id))

    async with aiosqlite.connect(settings.db_path) as conn:
        # Both messages must still exist.
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE run_id = ?", (run_id,)
        ) as cur:
            count_row = await cur.fetchone()
        assert count_row is not None
        assert count_row[0] == 2, (
            f"race guard must preserve both messages; found {count_row[0]}"
        )

        # The run status must still be 'ok'.
        async with conn.execute(
            "SELECT status FROM runs WHERE id = ?", (run_id,)
        ) as cur:
            run_row = await cur.fetchone()
        assert run_row is not None
        assert run_row[0] == "ok", (
            f"race guard must leave status='ok', got {run_row[0]!r}"
        )


# ---------------------------------------------------------------------------
# Test 4 — No-handle running run: DB cleanup fires without error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_no_handle_running_run_still_cleaned(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_run() must clean up a running run even when no broker handle exists
    (e.g. after a backend restart that lost in-memory state).
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)

    from paperhub.api.chat import CancelRequest, cancel_run

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.commit()
        await conn.execute(
            "INSERT INTO runs (session_id, status) VALUES (1, 'running')"
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        run_id = int(row[0])

        # Seed a user message.
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'hello', ?)",
            (run_id,),
        )
        await conn.commit()

    # Intentionally do NOT register a handle in the broker — simulates post-restart.
    import paperhub.api.chat as chat_module
    assert chat_module.broker.get(run_id) is None, "pre-condition: no handle registered"

    # Call cancel — must not raise.
    result = await cancel_run(CancelRequest(run_id=run_id))
    assert result == {"status": "cancelled", "run_id": str(run_id), "kept": False}

    async with aiosqlite.connect(settings.db_path) as conn:
        # Message must be gone.
        async with conn.execute(
            "SELECT id FROM messages WHERE run_id = ?", (run_id,)
        ) as cur:
            msg_row = await cur.fetchone()
        assert msg_row is None, "no-handle cancel must still delete the message row"

        # Run status must be 'cancelled'.
        async with conn.execute(
            "SELECT status FROM runs WHERE id = ?", (run_id,)
        ) as cur:
            run_row = await cur.fetchone()
        assert run_row is not None
        assert run_row[0] == "cancelled", (
            f"no-handle cancel must set status='cancelled', got {run_row[0]!r}"
        )


@pytest.mark.asyncio
async def test_cancel_keeps_a_turn_whose_actions_ran(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review #5: a stopped agent turn that already added a paper keeps its
    user message and an empty assistant stub (the chips replay on it); its
    still-pending confirm actions are dismissed."""
    import json

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)
    from paperhub.api.chat import CancelRequest, cancel_run

    settings = load_settings()
    record = {"cancelled": True, "ui_events": [
        {"event": "paper_added", "data": {"papers_id": 1, "paper_content_id": 1,
                                          "title": "T"}}]}
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute(
            "INSERT INTO runs (session_id, status, mode, agent_record_json) "
            "VALUES (1, 'running', 'agent', ?)", (json.dumps(record),))
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'add X and read it', 1)")
        await conn.execute(
            "INSERT INTO pending_actions (id, run_id, session_id, tool, args_json) "
            "VALUES ('p1', 1, 1, 'remove_paper', '{}')")
        await conn.commit()

    await cancel_run(CancelRequest(run_id=1))

    async with aiosqlite.connect(settings.db_path) as conn:
        async with conn.execute(
            "SELECT role, content FROM messages WHERE run_id = 1 ORDER BY id") as cur:
            rows = [tuple(r) for r in await cur.fetchall()]
        async with conn.execute("SELECT status FROM pending_actions") as cur:
            pend = await cur.fetchone()
        async with conn.execute("SELECT status FROM runs WHERE id = 1") as cur:
            run = await cur.fetchone()
    assert rows == [("user", "add X and read it"), ("assistant", "")]
    assert pend is not None and pend[0] == "dismissed"
    assert run is not None and run[0] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_keeps_the_stub_from_trace_evidence_when_the_record_is_late(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-review N1: the turn did not save its cancel record in time, but its
    trace shows add_paper ran: the stub is kept, the chip is rebuilt from the
    trace row, and the response says kept."""
    import json

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)
    from paperhub.api.chat import CancelRequest, cancel_run

    settings = load_settings()
    added = {"paper_id": "library:1", "added": True, "paper_content_id": 1,
             "papers_id": 4, "title": "T", "prior_state": "absent", "status": "ok"}
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute(
            "INSERT INTO runs (session_id, status, mode) VALUES (1, 'running', 'agent')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'add T', 1)")
        await conn.execute(
            "INSERT INTO tool_calls (run_id, step_index, agent, tool, latency_ms, status, "
            "result_summary_json) VALUES (1, 1, 'agent', 'tool:add_paper', 5, 'ok', ?)",
            (json.dumps(added),))
        await conn.commit()

    out = await cancel_run(CancelRequest(run_id=1))
    assert out["kept"] is True

    async with aiosqlite.connect(settings.db_path) as conn:
        async with conn.execute(
            "SELECT role, content FROM messages WHERE run_id = 1 ORDER BY id") as cur:
            rows = [tuple(r) for r in await cur.fetchall()]
        async with conn.execute("SELECT agent_record_json FROM runs WHERE id = 1") as cur:
            rec = await cur.fetchone()
    assert rows == [("user", "add T"), ("assistant", "")]
    assert rec is not None and json.loads(rec[0])["ui_events"] == [{
        "event": "paper_added",
        "data": {"papers_id": 4, "paper_content_id": 1, "title": "T"}}]


@pytest.mark.asyncio
async def test_cancel_reports_not_kept_without_side_effects(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)
    from paperhub.api.chat import CancelRequest, cancel_run

    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute("INSERT INTO runs (session_id, status) VALUES (1, 'running')")
        await conn.commit()
    out = await cancel_run(CancelRequest(run_id=1))
    assert out["kept"] is False


@pytest.mark.asyncio
async def test_trace_fallback_drops_chips_undone_later_in_the_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Residual D1: a paper added then removed (or a memory saved then
    forgotten) later in the same run gets no chip from the trace fallback."""
    import json

    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    await _bootstrap_schema(tmp_path)
    from paperhub.api.chat import _side_effects_from_trace

    rows = [
        ("tool:add_paper", {"added": True, "paper_content_id": 1, "papers_id": 4,
                            "title": "A", "prior_state": "absent"}),
        ("tool:add_paper", {"added": True, "paper_content_id": 2, "papers_id": 5,
                            "title": "B", "prior_state": "absent"}),
        ("tool:remember", {"saved": True, "memory_id": 7, "content": "m", "scope": "global"}),
        ("tool:remember", {"saved": True, "memory_id": 8, "content": "n", "scope": "session"}),
        ("tool:remove_paper", {"removed": True, "paper_content_id": 1, "title": "A"}),
        ("tool:forget", {"forgotten": True, "memory_id": 7}),
    ]
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute("INSERT INTO runs (session_id, status) VALUES (1, 'running')")
        for i, (tool, rec) in enumerate(rows):
            await conn.execute(
                "INSERT INTO tool_calls (run_id, step_index, agent, tool, latency_ms, "
                "status, result_summary_json) VALUES (1, ?, 'agent', ?, 1, 'ok', ?)",
                (i, tool, json.dumps(rec)))
        await conn.commit()
        events = await _side_effects_from_trace(conn, 1)
    assert [(e["event"], e["data"].get("papers_id") or e["data"].get("memory_id"))
            for e in events] == [("paper_added", 5), ("memory_saved", 8)]
