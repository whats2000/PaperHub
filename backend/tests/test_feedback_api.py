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
