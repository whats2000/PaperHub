"""POST /chat/actions/{id}/confirm|dismiss and PATCH /sessions/{id} (H4 Task 8)."""
from __future__ import annotations

import asyncio
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
    assert r.status_code == 200 and r.json() == {"status": "dismissed", "resumed": False}
    assert (await _one(db, "SELECT enabled FROM papers"))[0] == 1
    assert (await c.post("/chat/actions/a1/dismiss")).status_code == 409
    assert (await c.post("/chat/actions/a1/confirm")).status_code == 409
    assert (await c.post("/chat/actions/nope/confirm")).status_code == 404
    assert (await c.post("/chat/actions/nope/dismiss")).status_code == 404


async def test_patch_session_auto_approve(env: tuple[AsyncClient, Path]) -> None:
    c, db = env
    r = await c.patch("/sessions/1", json={"auto_approve": True})
    assert r.status_code == 200 and r.json() == {
        "id": 1, "auto_approve": True, "allow_searches": False}
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


async def test_paused_lists_the_cards_running_runs_wait_on(
    env: tuple[AsyncClient, Path],
) -> None:
    """H5a persisted state: a running run with pending cards is paused."""
    c, db = env
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE runs SET status = 'running'")
        await conn.execute(
            "UPDATE pending_actions SET result_json = '{\"claimed\": true}' "
            "WHERE id = 'a2'")
        await conn.execute(
            "INSERT INTO held_requests (id, created_at, caller, kind, destination, "
            "text, unattended, run_id, session_id, status) VALUES "
            "('h1', '2026-01-01', 'c', 'web_search', 'w', 'q', 0, 1, 1, 'pending'), "
            "('h2', '2026-01-01', 'c', 'web_search', 'w', 'q', 1, 1, 1, 'pending')")
        await conn.commit()
    r = await c.get("/chat/paused")
    assert sorted((x["kind"], x["id"]) for x in r.json()) == [
        ("action", "a1"), ("held", "h1")]
    assert all(x["session_id"] == 1 and x["run_id"] == 1 for x in r.json())
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE runs SET status = 'ok'")
        await conn.commit()
    assert (await c.get("/chat/paused")).json() == []


# ---------------------------------------------------------------- H5a wave 3
# The "Allow this search?" card of Ask first mode: a search_papers
# pending_actions row its paused run waits on as ``search:<id>``.

_SEARCH = (
    "INSERT INTO pending_actions (id, run_id, session_id, tool, args_json) "
    "VALUES (?, 1, 1, 'search_papers', '{\"query\": \"sparse priors\"}')"
)


async def _search_waiting(db: Path, action_id: str = "s1") -> asyncio.Task[object]:
    from paperhub import approvals

    async with aiosqlite.connect(db) as conn:
        await conn.execute(_SEARCH, (action_id,))
        await conn.commit()
    if not approvals.is_open(1):
        approvals.open_run(1)
    task = asyncio.create_task(approvals.wait(
        run_id=1, kind="search", row_id=action_id, tracer=None))
    await asyncio.sleep(0)
    return task  # type: ignore[return-value]


async def test_allow_search_hands_the_decision_to_the_paused_run(
    env: tuple[AsyncClient, Path],
) -> None:
    from paperhub import approvals

    c, db = env
    try:
        task = await _search_waiting(db)
        r = await c.post("/chat/actions/s1/confirm", json={})
        assert r.status_code == 200
        assert r.json() == {"status": "allowed", "text": "sparse priors",
                            "edited": False, "allow_chat": False, "resumed": True,
                            "also_allowed": []}
        decision = await task
        assert decision == approvals.ApprovalDecision(True, "sparse priors")
        # The run writes the final state; until then the row is claimed.
        state = (await c.get("/chat/actions/s1")).json()
        assert state["status"] == "running"
        assert state["result"]["text"] == "sparse priors"
        assert (await _one(db, "SELECT allow_searches FROM chat_sessions"))[0] == 0
    finally:
        approvals.close_run(1)


async def test_allow_searches_in_this_chat_with_an_edit(
    env: tuple[AsyncClient, Path],
) -> None:
    from paperhub import approvals

    c, db = env
    try:
        task = await _search_waiting(db)
        r = await c.post("/chat/actions/s1/confirm",
                         json={"text": "  sparsity segmentation ", "allow_chat": True})
        assert r.json()["text"] == "sparsity segmentation"
        assert r.json()["edited"] is True
        decision = await task
        assert decision == approvals.ApprovalDecision(
            True, "sparsity segmentation", edited=True, allow_chat=True)
        assert (await _one(db, "SELECT allow_searches FROM chat_sessions"))[0] == 1
        listed = await c.patch("/sessions/1", json={"allow_searches": False})
        assert listed.json()["allow_searches"] is False
    finally:
        approvals.close_run(1)


async def test_allow_searches_in_this_chat_allows_the_other_waiting_searches(
    env: tuple[AsyncClient, Path],
) -> None:
    """Parallel searches each wait on a card. "Allow searches in this chat"
    settles the chat's other waiting search cards with their shown query;
    held cards and actions still wait (they are not search permissions)."""
    from paperhub import approvals

    c, db = env
    try:
        first = await _search_waiting(db, "s1")
        second = await _search_waiting(db, "s2")
        third = await _search_waiting(db, "s3")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "UPDATE pending_actions SET args_json = ? WHERE id = 's3'",
                ('{"query": "wafer maps"}',))
            await conn.execute(
                "INSERT INTO pending_actions (id, run_id, session_id, tool, args_json) "
                "VALUES ('a9', 1, 1, 'remove_paper', '{}')")
            await conn.commit()
        action = asyncio.create_task(approvals.wait(
            run_id=1, kind="action", row_id="a9", tracer=None))
        await asyncio.sleep(0)

        r = await c.post("/chat/actions/s1/confirm", json={"allow_chat": True})
        assert r.status_code == 200
        assert sorted(r.json()["also_allowed"], key=lambda x: x["action_id"]) == [
            {"action_id": "s2", "text": "sparse priors"},
            {"action_id": "s3", "text": "wafer maps"}]
        assert (await first).allow_chat is True
        assert await second == approvals.ApprovalDecision(
            True, "sparse priors", allow_chat=True, by_chat_setting=True)
        assert await third == approvals.ApprovalDecision(
            True, "wafer maps", allow_chat=True, by_chat_setting=True)
        state = (await c.get("/chat/actions/s2")).json()
        assert state["status"] == "running"
        assert state["result"]["by_chat_setting"] is True
        # The action still waits for its own decision.
        await asyncio.sleep(0)
        assert not action.done()
        assert (await c.get("/chat/actions/a9")).json() == {"status": "pending"}
        action.cancel()
    finally:
        approvals.close_run(1)


async def test_plain_allow_leaves_the_other_waiting_searches(
    env: tuple[AsyncClient, Path],
) -> None:
    from paperhub import approvals

    c, db = env
    try:
        await _search_waiting(db, "s1")
        second = await _search_waiting(db, "s2")
        r = await c.post("/chat/actions/s1/confirm", json={})
        assert r.json()["also_allowed"] == []
        await asyncio.sleep(0)
        assert not second.done()
        second.cancel()
    finally:
        approvals.close_run(1)


async def test_an_edited_search_is_checked_for_sensitive_terms(
    env: tuple[AsyncClient, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperhub import approvals

    monkeypatch.setenv("PAPERHUB_SENSITIVE_TERMS", "KESTREL")
    c, db = env
    try:
        task = await _search_waiting(db)
        r = await c.post("/chat/actions/s1/confirm", json={"text": "kestrel defects"})
        assert r.status_code == 422
        assert r.json() == {"detail": "sensitive_term", "term": "KESTREL"}
        r = await c.post("/chat/actions/s1/confirm", json={"text": "   "})
        assert r.status_code == 422 and r.json()["detail"] == "empty_text"
        assert not task.done()
        assert (await c.get("/chat/actions/s1")).json() == {"status": "pending"}
    finally:
        approvals.close_run(1)


async def test_dont_allow_passes_the_note_to_the_run(
    env: tuple[AsyncClient, Path],
) -> None:
    from paperhub import approvals

    c, db = env
    try:
        task = await _search_waiting(db)
        r = await c.post("/chat/actions/s1/dismiss",
                         json={"note": " Only use papers in this chat. "})
        assert r.json() == {"status": "dismissed", "resumed": True}
        decision = await task
        assert decision == approvals.ApprovalDecision(
            False, note="Only use papers in this chat.")
        state = (await c.get("/chat/actions/s1")).json()
        assert state == {"status": "dismissed", "result": {
            "decision": "declined", "note": "Only use papers in this chat."}}
        long = await _search_waiting(db, "s2")
        r = await c.post("/chat/actions/s2/dismiss", json={"note": "x" * 1001})
        assert r.status_code == 422 and r.json()["detail"] == "note_too_long"
        long.cancel()
    finally:
        approvals.close_run(1)


async def test_a_search_card_whose_turn_ended_sends_nothing(
    env: tuple[AsyncClient, Path],
) -> None:
    c, db = env
    async with aiosqlite.connect(db) as conn:
        await conn.execute(_SEARCH, ("s1",))
        await conn.commit()
    r = await c.post("/chat/actions/s1/confirm", json={})
    assert r.status_code == 409 and r.json()["detail"] == "turn_ended"
    state = (await c.get("/chat/actions/s1")).json()
    assert state["status"] == "dismissed"
    assert (await _one(db, "SELECT COUNT(*) FROM tool_calls"))[0] == 0


async def test_paused_names_a_search_card(env: tuple[AsyncClient, Path]) -> None:
    c, db = env
    async with aiosqlite.connect(db) as conn:
        await conn.execute(_SEARCH, ("s1",))
        await conn.execute("DELETE FROM pending_actions WHERE tool = 'remove_paper'")
        await conn.commit()
    assert [(x["kind"], x["id"]) for x in (await c.get("/chat/paused")).json()] == [
        ("search", "s1")]
