"""Plan H2 Task 5: confirmation queue with full conversation context."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient


async def _run(conn: aiosqlite.Connection, sid: int) -> int:
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'ok')", (sid,),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _msg(
    conn: aiosqlite.Connection, sid: int, role: str, content: str,
    run_id: int | None = None,
) -> int:
    cur = await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (?,?,?,?)",
        (sid, role, content, run_id),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _implicit(
    conn: aiosqlite.Connection, run_id: int, *, trigger: str = "keyword",
    reasons: list[str] | None = None, rationale: str = "unsure",
    status: str = "needs_confirmation",
) -> int:
    cur = await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, reasons_json, trigger, "
        "verdict, status, reviewer_rationale) "
        "VALUES (?, 'implicit', 'down', ?, ?, 'uncertain', ?, ?)",
        (run_id, json.dumps(reasons or []), trigger, status, rationale),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _session(conn: aiosqlite.Connection, title: str = "Chat") -> int:
    cur = await conn.execute("INSERT INTO chat_sessions (title) VALUES (?)", (title,))
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _seed_keyword(conn: aiosqlite.Connection) -> tuple[int, int, int]:
    """Session with an earlier turn and a flagged answer; returns (sid, r1, r0)."""
    sid = await _session(conn, "Attention chat")
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, title, arxiv_id, source_path, source_dir_path, html_path) "
        "VALUES ('k1','arxiv','Attention Is All You Need','1706.1','x','x','x')",
    )
    pc = cur.lastrowid
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, title, arxiv_id, source_path, source_dir_path, html_path) "
        "VALUES ('k2','arxiv','Disabled Paper','2.2','x','x','x')",
    )
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (?,?,1)",
        (sid, pc),
    )
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) "
        "VALUES (?, (SELECT id FROM paper_content WHERE content_key='k2'), 0)",
        (sid,),
    )
    r0 = await _run(conn, sid)
    await _msg(conn, sid, "user", "hello", r0)
    await _msg(conn, sid, "assistant", "hi", r0)
    r1 = await _run(conn, sid)
    await _msg(conn, sid, "user", "what is the model?", r1)
    await _msg(conn, sid, "assistant", "a CNN", r1)
    return sid, r1, r0


async def test_queue_item_has_full_context_in_order(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid, r1, _ = await _seed_keyword(conn)
    r2 = await _run(conn, sid)
    await _msg(conn, sid, "user", "that is wrong", r2)
    await _msg(conn, sid, "assistant", "sorry, a Transformer", r2)
    fid = await _implicit(conn, r1, reasons=["factual"], rationale="user disagrees")
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/feedback/queue")
    assert r.status_code == 200
    [item] = r.json()
    assert item == {
        "feedback_id": fid, "run_id": r1, "session_id": sid,
        "session_title": "Attention chat",
        "papers": ["Attention Is All You Need"],
        "earlier": [{"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"}],
        "question": "what is the model?", "answer": "a CNN",
        "flagged": "that is wrong", "next_reply": "sorry, a Transformer",
        "reviewer_doubt": "user disagrees", "guessed_reasons": ["factual"],
    }


async def test_next_reply_null_when_user_has_not_continued(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid, r1, _ = await _seed_keyword(conn)
    await _msg(conn, sid, "user", "nope, wrong")
    await _implicit(conn, r1)
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        [item] = (await c.get("/feedback/queue")).json()
    assert item["flagged"] == "nope, wrong"
    assert item["next_reply"] is None


async def test_queue_excludes_final_and_explicit_and_falls_back_to_guess(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    _, r1, r0 = await _seed_keyword(conn)
    await conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, "
        "result_summary_json, latency_ms, status) "
        "VALUES (?, 0, 'router', 'classify', ?, 1, 'ok')",
        (r1, json.dumps({"intent": "paper_qa"})),
    )
    await _implicit(conn, r0, status="final")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'down')",
        (r0,),
    )
    await _implicit(conn, r1, reasons=[])
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        items = (await c.get("/feedback/queue")).json()
    assert [i["run_id"] for i in items] == [r1]
    assert isinstance(items[0]["guessed_reasons"], list)


async def test_fork_flagged_is_first_new_user_message(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid, r1, _ = await _seed_keyword(conn)
    fid = await _implicit(conn, r1, trigger="fork")
    fork = await _session(conn, "Fork")
    await conn.execute(
        "UPDATE chat_sessions SET forked_from_session_id=?, forked_from_run_id=? "
        "WHERE id=?", (sid, r1, fork),
    )
    f0 = await _run(conn, fork)
    await _msg(conn, fork, "user", "hello", f0)       # copied
    await _msg(conn, fork, "assistant", "hi", f0)     # copied
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        [item] = (await c.get("/feedback/queue")).json()
        assert item["flagged"] == "" and item["next_reply"] is None
        f1 = await _run(conn, fork)
        await _msg(conn, fork, "user", "what is the architecture?", f1)
        await _msg(conn, fork, "assistant", "a Transformer", f1)
        await conn.commit()
        [item] = (await c.get("/feedback/queue")).json()
    assert item["feedback_id"] == fid
    assert item["flagged"] == "what is the architecture?"
    assert item["next_reply"] == "a Transformer"


async def test_submit_two_of_three_leaves_one(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    _, r1, r0 = await _seed_keyword(conn)
    # One implicit row per (run, trigger) — idx_feedback_implicit_run_trigger.
    ids = [await _implicit(conn, r1), await _implicit(conn, r1, trigger="fork"),
           await _implicit(conn, r0)]
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/queue/submit", json={"answers": [
            {"feedback_id": ids[0], "confirmed": True,
             "reasons": ["factual", "citation"]},
            {"feedback_id": ids[1], "confirmed": False, "reasons": ["factual"]},
        ]})
        assert r.status_code == 200
        assert r.json() == {"confirmed": 1, "rejected": 1}
        left = (await c.get("/feedback/queue")).json()
    assert [i["feedback_id"] for i in left] == [ids[2]]
    async with conn.execute(
        "SELECT id, status, verdict, reasons_json FROM feedback ORDER BY id",
    ) as cur:
        rows = [tuple(x) for x in await cur.fetchall()]
    assert rows[0] == (ids[0], "final", "confirmed", '["factual", "citation"]')
    assert rows[1] == (ids[1], "final", "rejected", "[]")
    assert rows[2][1:3] == ("needs_confirmation", "uncertain")


async def test_submit_unknown_id_is_404_and_changes_nothing(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    _, r1, _ = await _seed_keyword(conn)
    fid = await _implicit(conn, r1)
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/queue/submit", json={"answers": [
            {"feedback_id": fid, "confirmed": True, "reasons": ["factual"]},
            {"feedback_id": 9999, "confirmed": True, "reasons": []},
        ]})
        assert r.status_code == 404
        assert len((await c.get("/feedback/queue")).json()) == 1


async def test_submit_unknown_reason_is_422(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    _, r1, _ = await _seed_keyword(conn)
    fid = await _implicit(conn, r1)
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/queue/submit", json={"answers": [
            {"feedback_id": fid, "confirmed": True, "reasons": ["bogus"]},
        ]})
    assert r.status_code == 422


async def test_implicit_rows_absent_from_session_runs_listing(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid, r1, _ = await _seed_keyword(conn)
    await _implicit(conn, r1, status="final")
    await _implicit(conn, r1, trigger="fork")
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/feedback/runs", params={"session_id": sid})
    assert r.json() == []


async def test_queue_skips_a_run_the_user_already_rated(
    app_with_db: tuple[Any, Any],
) -> None:
    """Fix M4: an explicit thumbs answers the question the queue would ask."""
    from paperhub.feedback.repo import build_summary

    app, conn = app_with_db
    _, r1, r0 = await _seed_keyword(conn)
    await _implicit(conn, r0)
    await _implicit(conn, r1)
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'up')",
        (r1,),
    )
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        items = (await c.get("/feedback/queue")).json()
    assert [i["run_id"] for i in items] == [r0]
    assert (await build_summary(conn)).kpis.pending == 1


async def _states(conn: aiosqlite.Connection, run_id: int) -> list[tuple[Any, ...]]:
    async with conn.execute(
        "SELECT status, verdict FROM feedback WHERE run_id = ? AND kind = 'implicit'",
        (run_id,),
    ) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def test_explicit_rating_resolves_the_pending_reviewer_item(
    app_with_db: tuple[Any, Any],
) -> None:
    """U2: the item must not stay ``needs_confirmation`` forever, and a
    confirmed fork guess must not become a counted negative by being closed."""
    from paperhub.feedback.repo import upsert_explicit

    _, conn = app_with_db
    _, r1, _ = await _seed_keyword(conn)
    fid = await _implicit(conn, r1, trigger="fork")
    await conn.execute("UPDATE feedback SET verdict = 'confirmed' WHERE id = ?", (fid,))
    await conn.commit()
    await upsert_explicit(conn, r1, "up", [], None, None)
    assert await _states(conn, r1) == [("final", "rejected")]


async def test_superseded_rows_migrate_and_the_migration_is_idempotent(
    app_with_db: tuple[Any, Any],
) -> None:
    from paperhub.feedback.repo import resolve_superseded

    _, conn = app_with_db
    _, r1, r0 = await _seed_keyword(conn)
    await _implicit(conn, r0)
    await _implicit(conn, r1)
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'down')",
        (r1,),
    )
    await conn.commit()
    assert await resolve_superseded(conn, dry_run=True) == 1
    assert await _states(conn, r1) == [("needs_confirmation", "uncertain")]
    assert await resolve_superseded(conn) == 1
    assert await _states(conn, r1) == [("final", "rejected")]
    assert await _states(conn, r0) == [("needs_confirmation", "uncertain")]
    assert await resolve_superseded(conn) == 0
