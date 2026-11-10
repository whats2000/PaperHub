"""POST /chat/actions/{id}/confirm|dismiss and PATCH /sessions/{id} (H4 Task 8)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio

_PENDING = (
    "INSERT INTO pending_actions (id, run_id, session_id, tool, args_json) "
    "VALUES (?, 1, 1, 'remove_paper', ?)"
)


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, Path]]:
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES ('arxiv:1', 'arxiv', '1', 'T1', '/x', '/x', '/x')")
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) "
            "VALUES (1, 1, 1)")
        await conn.execute(_PENDING, ("a1", '{"paper": 1}'))
        await conn.execute(_PENDING, ("a2", '{"paper": 1}'))
        await conn.commit()
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        yield c, db_path


async def _one(db: Path, sql: str) -> tuple[object, ...]:
    async with aiosqlite.connect(db) as conn, conn.execute(sql) as cur:
        row = await cur.fetchone()
    assert row is not None
    return tuple(row)


async def test_confirm_runs_handler_once(env: tuple[AsyncClient, Path]) -> None:
    c, db = env
    r = await c.post("/chat/actions/a1/confirm")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert (await _one(db, "SELECT enabled FROM papers"))[0] == 0
    assert (await _one(
        db, "SELECT status FROM pending_actions WHERE id='a1'"))[0] == "done"
    assert (await _one(
        db, "SELECT COUNT(*) FROM tool_calls WHERE tool='tool:remove_paper'"))[0] == 1
    assert (await c.post("/chat/actions/a1/confirm")).status_code == 409


async def test_confirm_failure_stores_failed(env: tuple[AsyncClient, Path]) -> None:
    c, db = env
    await c.post("/chat/actions/a1/confirm")  # the paper is now disabled
    r = await c.post("/chat/actions/a2/confirm")
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert (await _one(
        db, "SELECT status FROM pending_actions WHERE id='a2'"))[0] == "failed"


async def test_dismiss_and_errors(env: tuple[AsyncClient, Path]) -> None:
    c, db = env
    r = await c.post("/chat/actions/a1/dismiss")
    assert r.status_code == 200 and r.json() == {"status": "dismissed"}
    assert (await _one(db, "SELECT enabled FROM papers"))[0] == 1
    assert (await c.post("/chat/actions/a1/dismiss")).status_code == 409
    assert (await c.post("/chat/actions/a1/confirm")).status_code == 409
    assert (await c.post("/chat/actions/nope/confirm")).status_code == 404
    assert (await c.post("/chat/actions/nope/dismiss")).status_code == 404


async def test_patch_session_auto_approve(env: tuple[AsyncClient, Path]) -> None:
    c, db = env
    r = await c.patch("/sessions/1", json={"auto_approve": True})
    assert r.status_code == 200 and r.json() == {"id": 1, "auto_approve": True}
    assert (await _one(db, "SELECT auto_approve FROM chat_sessions"))[0] == 1
    assert (await c.patch("/sessions/99", json={"auto_approve": True})).status_code == 404
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (1, 'user', 'hi')")
        await conn.commit()
    listed = (await c.get("/sessions")).json()
    assert listed[0]["auto_approve"] is True


async def test_concurrent_confirms_on_one_run_do_not_collide(
    env: tuple[AsyncClient, Path],
) -> None:
    import asyncio

    c, db = env
    rs = await asyncio.gather(
        c.post("/chat/actions/a1/confirm"), c.post("/chat/actions/a2/confirm"),
    )
    assert [r.status_code for r in rs] == [200, 200]
    assert (await _one(
        db, "SELECT COUNT(DISTINCT step_index), COUNT(*) FROM tool_calls "
            "WHERE run_id = 1 AND tool = 'tool:remove_paper'")) == (2, 2)


async def test_confirm_with_existing_newer_steps(env: tuple[AsyncClient, Path]) -> None:
    c, db = env
    async with aiosqlite.connect(db) as conn:
        for i in range(5):
            await conn.execute(
                "INSERT INTO tool_calls (run_id, branch, step_index, agent, tool, status, latency_ms) "
                "VALUES (1, '', ?, 'agent', 'tool:x', 'ok', 0)", (i,))
        await conn.commit()
    assert (await c.post("/chat/actions/a1/confirm")).status_code == 200
    assert (await _one(
        db, "SELECT step_index FROM tool_calls WHERE tool='tool:remove_paper'"))[0] == 5


async def test_confirm_setup_failure_marks_failed(
    env: tuple[AsyncClient, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paperhub.api.actions as actions

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("ctx exploded")

    monkeypatch.setattr(actions, "ToolContext", boom)
    c, db = env
    r = await c.post("/chat/actions/a1/confirm")
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert "ctx exploded" in r.json()["error"]
    assert (await _one(
        db, "SELECT status FROM pending_actions WHERE id='a1'"))[0] == "failed"
    assert (await c.post("/chat/actions/a1/confirm")).status_code == 409


async def test_get_action_reports_its_current_state(
    env: tuple[AsyncClient, Path],
) -> None:
    c, _db = env
    assert (await c.get("/chat/actions/a1")).json() == {"status": "pending"}
    await c.post("/chat/actions/a1/confirm")
    done = (await c.get("/chat/actions/a1")).json()
    assert done["status"] == "done" and "content" in done["result"]
    await c.post("/chat/actions/a2/dismiss")
    assert (await c.get("/chat/actions/a2")).json() == {"status": "dismissed"}
    assert (await c.get("/chat/actions/nope")).status_code == 404


async def test_confirm_refused_for_a_cancelled_run(
    env: tuple[AsyncClient, Path],
) -> None:
    """Review #5: an action whose turn was stopped can't be executed later
    (stale tab); the row is left untouched."""
    c, db = env
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE runs SET status = 'cancelled' WHERE id = 1")
        await conn.commit()
    r = await c.post("/chat/actions/a1/confirm")
    assert r.status_code == 409
    assert (await _one(db, "SELECT status FROM pending_actions WHERE id='a1'"))[0] == "pending"
    r = await c.post("/chat/actions/a1/dismiss")
    assert r.status_code == 200


async def test_confirm_refused_for_a_deleted_chat(
    env: tuple[AsyncClient, Path],
) -> None:
    c, db = env
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE chat_sessions SET deleted_at = datetime('now')")
        await conn.commit()
    r = await c.post("/chat/actions/a1/confirm")
    assert r.status_code == 409


async def test_confirm_cancelled_mid_execution_is_marked_failed(
    env: tuple[AsyncClient, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review #11: a confirm whose execution is cancelled (server shutdown)
    never leaves the row claimed."""
    import asyncio

    from paperhub.api import actions

    async def cancelled(*_a: object, **_k: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(actions, "_execute", cancelled)
    c, db = env
    with pytest.raises(asyncio.CancelledError):
        await actions.confirm_action("a1", None)  # type: ignore[arg-type]
    row = await _one(db, "SELECT status, result_json FROM pending_actions WHERE id='a1'")
    assert row[0] == "failed" and "interrupted" in str(row[1])
