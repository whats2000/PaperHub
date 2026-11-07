"""Plan H2 Task 2: explicit thumbs feedback REST surface + upsert semantics."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

from paperhub.feedback.repo import upsert_explicit


async def _seed_run(
    conn: aiosqlite.Connection, *, session_id: int | None = None,
    intent: str = "paper_qa", answer: str = "Answer [chunk:1]",
) -> tuple[int, int]:
    if session_id is None:
        cur = await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        assert cur.lastrowid is not None
        session_id = int(cur.lastrowid)
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'ok')", (session_id,),
    )
    assert cur.lastrowid is not None
    run_id = int(cur.lastrowid)
    await conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, "
        "result_summary_json, latency_ms, status) "
        "VALUES (?, 0, 'router', 'classify', ?, 1, 'ok')",
        (run_id, json.dumps({"intent": intent, "reasoning": "content question"})),
    )
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'assistant', ?, ?)",
        (session_id, answer, run_id),
    )
    await conn.commit()
    return session_id, run_id


async def _rows(conn: aiosqlite.Connection, run_id: int) -> list[tuple[Any, ...]]:
    async with conn.execute(
        "SELECT kind, polarity, reasons_json, other_text, correction, message_id "
        "FROM feedback WHERE run_id = ?", (run_id,),
    ) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def test_upsert_down_then_up_leaves_one_cleared_row(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, run = await _seed_run(migrated_db)
    await upsert_explicit(
        migrated_db, run, "down", ["citation", "other"], "too vague", "fix it",
    )
    rows = await _rows(migrated_db, run)
    assert len(rows) == 1
    assert rows[0][:4] == ("explicit", "down", '["citation", "other"]', "too vague")
    assert rows[0][4] == "fix it"
    assert rows[0][5] is not None  # linked to the assistant message

    await upsert_explicit(migrated_db, run, "up", ["citation"], "x", None)
    rows = await _rows(migrated_db, run)
    assert len(rows) == 1
    assert rows[0][:4] == ("explicit", "up", "[]", None)


async def test_upsert_none_deletes_only_the_explicit_row(
    migrated_db: aiosqlite.Connection,
) -> None:
    _, run = await _seed_run(migrated_db)
    await migrated_db.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'implicit', 'down')",
        (run,),
    )
    await migrated_db.commit()
    await upsert_explicit(migrated_db, run, "down", ["factual"], None, None)
    await upsert_explicit(migrated_db, run, None, [], None, None)
    rows = await _rows(migrated_db, run)
    assert [r[0] for r in rows] == ["implicit"]


async def test_put_get_and_clear_round_trip(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    sid, run_a = await _seed_run(conn)
    _, run_b = await _seed_run(conn, session_id=sid)
    _, other_run = await _seed_run(conn)  # a different session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put(f"/feedback/runs/{run_a}",
                        json={"polarity": "down", "reasons": ["citation", "other"],
                              "other_text": "too vague", "correction": "use table 2"})
        assert r.status_code == 200
        assert r.json() == {"run_id": run_a, "polarity": "down",
                            "reasons": ["citation", "other"],
                            "other_text": "too vague", "correction": "use table 2"}
        r = await c.put(f"/feedback/runs/{run_b}", json={"polarity": "up", "reasons": []})
        assert r.status_code == 200
        r = await c.put(f"/feedback/runs/{other_run}", json={"polarity": "up"})
        assert r.status_code == 200

        r = await c.get("/feedback/runs", params={"session_id": sid})
        assert r.status_code == 200
        assert r.json() == [
            {"run_id": run_a, "polarity": "down", "reasons": ["citation", "other"],
             "other_text": "too vague", "correction": "use table 2"},
            {"run_id": run_b, "polarity": "up", "reasons": [],
             "other_text": None, "correction": None},
        ]

        r = await c.put(f"/feedback/runs/{run_a}", json={"polarity": "up",
                                                          "reasons": ["citation"]})
        assert r.json() == {"run_id": run_a, "polarity": "up", "reasons": [],
                            "other_text": None, "correction": None}

        r = await c.put(f"/feedback/runs/{run_a}", json={"polarity": None})
        assert r.status_code == 200
        r = await c.get("/feedback/runs", params={"session_id": sid})
        assert [x["run_id"] for x in r.json()] == [run_b]


async def test_put_unknown_run_is_404(app_with_db: tuple[Any, Any]) -> None:
    app, _ = app_with_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put("/feedback/runs/4242", json={"polarity": "up"})
    assert r.status_code == 404


async def test_put_unknown_reason_is_422(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    _, run = await _seed_run(conn)
    await conn.execute(
        "INSERT INTO feedback_presets (label, stage) VALUES ('too long', 'answer')",
    )
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put(f"/feedback/runs/{run}",
                        json={"polarity": "down", "reasons": ["made_up"]})
        assert r.status_code == 422
        r = await c.put(f"/feedback/runs/{run}",
                        json={"polarity": "down", "reasons": ["too long", "other"],
                              "other_text": "rambles"})
        assert r.status_code == 200


async def test_guess_endpoint(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    _, run = await _seed_run(conn, answer="No markers.")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/feedback/runs/{run}/guess")
        assert r.status_code == 200
        assert r.json() == {"reasons": ["citation"]}
        r = await c.get("/feedback/runs/4242/guess")
        assert r.status_code == 404


async def _bare_run(
    conn: aiosqlite.Connection, *, status: str, routing: str | None,
    assistant: bool,
) -> int:
    cur = await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    sid = int(cur.lastrowid or 0)
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status, routing_decision_json) VALUES (?,?,?)",
        (sid, status, routing),
    )
    rid = int(cur.lastrowid or 0)
    if assistant:
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (?, 'assistant', 'a', ?)", (sid, rid),
        )
    await conn.commit()
    return rid


async def test_put_rejects_runs_that_are_not_finished_chat_answers(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    chat = '{"intent": "paper_qa"}'
    running = await _bare_run(conn, status="running", routing=chat, assistant=False)
    review = await _bare_run(
        conn, status="ok", routing='{"intent": "feedback_review"}', assistant=False,
    )
    ideation = await _bare_run(
        conn, status="ok", routing='{"ideation_mode": "explore"}', assistant=True,
    )
    no_answer = await _bare_run(conn, status="ok", routing=chat, assistant=False)
    errored = await _bare_run(conn, status="error", routing=chat, assistant=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = {"polarity": "up"}
        assert (await c.put(f"/feedback/runs/{running}", json=body)).status_code == 409
        for rid in (review, ideation, no_answer):
            assert (await c.put(f"/feedback/runs/{rid}", json=body)).status_code == 422
        # An errored chat answer is still a visible answer: it may be rated.
        assert (await c.put(f"/feedback/runs/{errored}", json=body)).status_code == 200
        # Clearing never needs a valid target.
        assert (await c.put(f"/feedback/runs/{review}",
                            json={"polarity": None})).status_code == 200
    async with conn.execute("SELECT run_id FROM feedback") as cur:
        assert [r[0] for r in await cur.fetchall()] == [errored]


async def test_put_caps_free_text_length(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    _, run = await _seed_run(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put(f"/feedback/runs/{run}", json={
            "polarity": "down", "reasons": ["other"], "other_text": "x" * 501})
        assert r.status_code == 422
        r = await c.put(f"/feedback/runs/{run}", json={
            "polarity": "down", "correction": "x" * 8001})
        assert r.status_code == 422
        r = await c.put(f"/feedback/runs/{run}", json={
            "polarity": "down", "reasons": ["other"], "other_text": "x" * 500,
            "correction": "y" * 8000})
        assert r.status_code == 200


async def test_session_feedback_count_covers_explicit_and_reviewer_rows(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid, run = await _seed_run(conn)
    _, other = await _seed_run(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get(f"/feedback/sessions/{sid}/count")).json() == {"count": 0}
        await c.put(f"/feedback/runs/{run}", json={"polarity": "up"})
        await conn.execute(
            "INSERT INTO feedback (run_id, kind, polarity, trigger, verdict, status) "
            "VALUES (?, 'implicit', 'down', 'keyword', 'rejected', 'final')", (run,),
        )
        await conn.commit()
        assert (await c.get(f"/feedback/sessions/{sid}/count")).json() == {"count": 2}
        assert (await c.get(f"/feedback/sessions/{other}/count")).json() == {"count": 0}
