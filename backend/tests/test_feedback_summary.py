"""Plan H2 Task 6: summary, cases, promoted presets (+ Task 5 carried items)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

from paperhub.feedback.repo import build_summary

# Wednesday 2026-11-04 -> ISO weeks start (Mon): 10-12, 10-19, 10-26, 11-02.
NOW = datetime(2026, 11, 4, 12, 0, 0)


async def _session(conn: aiosqlite.Connection, deleted: bool = False) -> int:
    cur = await conn.execute(
        "INSERT INTO chat_sessions (title, deleted_at) VALUES ('c', ?)",
        ("2026-01-01" if deleted else None,),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _run(conn: aiosqlite.Connection, sid: int, question: str = "q") -> int:
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'ok')", (sid,),
    )
    assert cur.lastrowid is not None
    rid = int(cur.lastrowid)
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (?,?,?,?)",
        (sid, "user", question, rid),
    )
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (?,?,?,?)",
        (sid, "assistant", "a", rid),
    )
    return rid


async def _fb(
    conn: aiosqlite.Connection, run_id: int, kind: str, polarity: str,
    reasons: list[str], created: str, *, other: str | None = None,
    status: str = "final", verdict: str | None = None,
) -> int:
    if kind == "implicit" and verdict is None:
        verdict = "confirmed"
    cur = await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, reasons_json, other_text, "
        "trigger, verdict, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, kind, polarity, json.dumps(reasons), other,
         "keyword" if kind == "implicit" else None, verdict, status, created),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _seed(conn: aiosqlite.Connection) -> dict[str, int]:
    ids: dict[str, int] = {}
    sid = await _session(conn)
    r = [await _run(conn, sid, f"question {i}") for i in range(9)]
    ids["r"] = r[0]
    # explicit up (this week)
    await _fb(conn, r[0], "explicit", "up", [], "2026-11-03 10:00:00")
    # explicit down: two answer-stage reasons -> ONE answer count; + router
    ids["e_multi"] = await _fb(
        conn, r[1], "explicit", "down", ["factual", "off_topic", "wrong_route"],
        "2026-11-03 09:00:00",
    )
    # explicit down last week (week boundary: Sun 2026-11-01 is previous week)
    await _fb(conn, r[2], "explicit", "down", ["citation"], "2026-11-01 23:59:59")
    # explicit down with other text (+ case/space variants), no stage
    await _fb(conn, r[3], "explicit", "down", ["other"], "2026-10-20 08:00:00",
              other="  Too Long ")
    await _fb(conn, r[4], "explicit", "down", ["other"], "2026-10-27 08:00:00",
              other="too long")
    # confirmed implicit -> retrieval, current week (Mon 2026-11-02 00:00)
    ids["i_conf"] = await _fb(
        conn, r[5], "implicit", "down", ["wrong_source"], "2026-11-02 00:00:00",
    )
    # implicit that must NOT count: rejected, uncertain/pending, older than 4 wks
    await _fb(conn, r[6], "implicit", "down", [], "2026-11-03 00:00:00",
              verdict="rejected")
    await _fb(conn, r[7], "implicit", "down", ["factual"], "2026-11-03 00:00:00",
              verdict="uncertain", status="needs_confirmation")
    await _fb(conn, r[8], "implicit", "down", ["factual"], "2026-09-01 00:00:00")
    # soft-deleted session: excluded from everything
    dsid = await _session(conn, deleted=True)
    dr = await _run(conn, dsid)
    await _fb(conn, dr, "explicit", "down", ["router"], "2026-11-03 00:00:00")
    await _fb(conn, dr, "implicit", "down", [], "2026-11-03 00:00:00",
              verdict="uncertain", status="needs_confirmation")
    await conn.commit()
    return ids


async def test_summary_kpis_stages_weekly_and_other(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    await _seed(conn)
    s = (await build_summary(conn, NOW)).model_dump()
    assert s["kpis"] == {
        # runs: r0,r1,r2,r3,r4,r5 + r8 (old confirmed implicit still counts)
        "answers_with_feedback": 7,
        "approval_rate": 1 / 5,       # 1 up of 5 explicit
        "negatives_explicit": 4,
        "negatives_implicit": 2,      # r5 + r8 (rejected/uncertain excluded)
        "pending": 1,                 # r7 only (deleted session excluded)
    }
    by = {x["stage"]: x for x in s["stages"]}
    assert [x["stage"] for x in s["stages"]] == [
        "router", "retrieval", "citation", "answer"]
    assert by["router"] == {"stage": "router", "explicit": 1, "implicit": 0,
                            "weekly": [0, 0, 0, 1], "delta": 1}
    assert by["retrieval"] == {"stage": "retrieval", "explicit": 0, "implicit": 1,
                               "weekly": [0, 0, 0, 1], "delta": 1}
    # citation row at Sun 11-01 23:59 belongs to the PREVIOUS week
    assert by["citation"]["weekly"] == [0, 0, 1, 0]
    assert by["citation"]["delta"] == -1
    # factual+off_topic on one row = one answer count; r8 implicit is 2026-09
    assert by["answer"]["explicit"] == 1
    assert by["answer"]["implicit"] == 1
    assert by["answer"]["weekly"] == [0, 0, 0, 1]
    assert s["other_reasons"] == [{"text": "too long", "count": 2}]


async def test_summary_empty_has_null_approval(app_with_db: tuple[Any, Any]) -> None:
    _, conn = app_with_db
    s = await build_summary(conn, NOW)
    assert s.kpis.approval_rate is None
    assert s.kpis.answers_with_feedback == 0
    assert all(st.weekly == [0, 0, 0, 0] and st.delta == 0 for st in s.stages)


async def test_summary_endpoint_and_cases(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    ids = await _seed(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/feedback/summary")
        assert r.status_code == 200
        assert r.json()["kpis"]["negatives_explicit"] == 4
        allc = (await c.get("/feedback/cases")).json()
        # 4 explicit down + 2 confirmed implicit, newest id first, no deleted
        assert len(allc) == 6
        assert allc[0]["feedback_id"] > allc[-1]["feedback_id"]
        ret = (await c.get("/feedback/cases", params={"stage": "retrieval"})).json()
        assert [x["feedback_id"] for x in ret] == [ids["i_conf"]]
        assert ret[0]["kind"] == "implicit"
        assert ret[0]["question"] == "question 5"
        ans = (await c.get("/feedback/cases", params={"stage": "answer"})).json()
        assert ids["e_multi"] in {x["feedback_id"] for x in ans}
        assert (await c.get("/feedback/cases", params={"stage": "bogus"})
                ).status_code == 422


async def test_preset_counts_in_its_stage_and_is_allowed(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid = await _session(conn)
    r1 = await _run(conn, sid)
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/feedback/presets")).json() == []
        p = await c.post("/feedback/presets", json={"label": "too_long",
                                                    "stage": "answer"})
        assert p.status_code == 200 and p.json() == {"label": "too_long"}
        assert (await c.get("/feedback/presets")).json() == [
            {"label": "too_long", "stage": "answer"}]
        dup = await c.post("/feedback/presets", json={"label": "too_long",
                                                      "stage": "router"})
        assert dup.status_code == 409
        # built-in reason labels are taken too
        assert (await c.post("/feedback/presets", json={
            "label": "factual", "stage": "answer"})).status_code == 409
        # the preset is now an accepted reason and counts in ITS stage
        put = await c.put(f"/feedback/runs/{r1}", json={
            "polarity": "down", "reasons": ["too_long", "wrong_route"]})
        assert put.status_code == 200
        s = (await c.get("/feedback/summary")).json()
    by = {x["stage"]: x for x in s["stages"]}
    assert by["answer"]["explicit"] == 1 and by["router"]["explicit"] == 1


# ---- carried Task 5 review items -------------------------------------------

async def _queue_item(conn: aiosqlite.Connection, sid: int, run: int,
                      reasons: list[str] | None = None) -> int:
    cur = await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, reasons_json, trigger, "
        "verdict, status) VALUES (?, 'implicit', 'down', ?, 'keyword', "
        "'uncertain', 'needs_confirmation')",
        (run, json.dumps(reasons or [])),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def test_queue_guessed_reasons_fall_back_to_trace_guess(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid = await _session(conn)
    r = await _run(conn, sid)
    await conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, "
        "result_summary_json, latency_ms, status) "
        "VALUES (?, 0, 'router', 'classify', ?, 1, 'ok')",
        (r, json.dumps({"intent": "clarify"})),
    )
    await _queue_item(conn, sid, r, reasons=[])
    r2 = await _run(conn, sid)
    await _queue_item(conn, sid, r2, reasons=["citation"])  # reviewer wins
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        items = (await c.get("/feedback/queue")).json()
    assert [i["guessed_reasons"] for i in items] == [["wrong_route"], ["citation"]]


async def test_queue_excludes_soft_deleted_sessions(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid = await _session(conn, deleted=True)
    await _queue_item(conn, sid, await _run(conn, sid))
    live = await _session(conn)
    live_run = await _run(conn, live)
    await _queue_item(conn, live, live_run)
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        items = (await c.get("/feedback/queue")).json()
    assert [i["run_id"] for i in items] == [live_run]


async def test_submit_duplicate_ids_is_422(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    sid = await _session(conn)
    fid = await _queue_item(conn, sid, await _run(conn, sid))
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/queue/submit", json={"answers": [
            {"feedback_id": fid, "confirmed": True, "reasons": []},
            {"feedback_id": fid, "confirmed": False, "reasons": []},
        ]})
        assert r.status_code == 422
        assert len((await c.get("/feedback/queue")).json()) == 1


async def test_submit_validates_reasons_only_for_confirmed(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    sid = await _session(conn)
    fid = await _queue_item(conn, sid, await _run(conn, sid))
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/queue/submit", json={"answers": [
            {"feedback_id": fid, "confirmed": False, "reasons": ["bogus"]},
        ]})
    assert r.status_code == 200 and r.json() == {"confirmed": 0, "rejected": 1}


# ---- carried Task 6 review items -------------------------------------------

async def test_preset_collision_is_case_insensitive_and_length_capped(
    app_with_db: tuple[Any, Any],
) -> None:
    app, _ = app_with_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        ok = await c.post("/feedback/presets", json={"label": "Too long",
                                                     "stage": "answer"})
        assert ok.status_code == 200
        for label in ("too LONG", "  too long  ", "Factual", "OTHER", " other"):
            r = await c.post("/feedback/presets", json={"label": label,
                                                        "stage": "answer"})
            assert r.status_code == 409, label
        assert (await c.post("/feedback/presets", json={
            "label": "x" * 61, "stage": "answer"})).status_code == 422
        assert (await c.post("/feedback/presets", json={
            "label": "y" * 60, "stage": "answer"})).status_code == 200
        # non-ASCII case variants collide too (casefold, not SQLite lower)
        assert (await c.post("/feedback/presets", json={
            "label": "Ärger", "stage": "answer"})).status_code == 200
        assert (await c.post("/feedback/presets", json={
            "label": "ärger", "stage": "answer"})).status_code == 409
        assert (await c.post("/feedback/presets", json={
            "label": "Straße", "stage": "answer"})).status_code == 200
        assert (await c.post("/feedback/presets", json={
            "label": "STRASSE", "stage": "answer"})).status_code == 409


async def test_summary_weekly_buckets_zero_and_one(
    app_with_db: tuple[Any, Any],
) -> None:
    _, conn = app_with_db
    sid = await _session(conn)
    rows = [
        ("2026-10-12 00:00:00", "wrong_route"),  # Mon of week 0
        ("2026-10-18 23:59:59", "wrong_route"),  # Sun of week 0
        ("2026-10-19 00:00:00", "citation"),     # Mon of week 1
        ("2026-10-11 23:59:59", "citation"),     # before the window
    ]
    for created, reason in rows:
        r = await _run(conn, sid)
        await conn.execute(
            "INSERT INTO feedback (run_id, kind, polarity, reasons_json, created_at) "
            "VALUES (?, 'explicit', 'down', ?, ?)",
            (r, json.dumps([reason]), created),
        )
    await conn.commit()
    s = await build_summary(conn, datetime(2026, 11, 4, 12, 0, 0))
    by = {x.stage: x for x in s.stages}
    assert by["router"].weekly == [2, 0, 0, 0]
    assert by["citation"].weekly == [0, 1, 0, 0]
    assert by["citation"].explicit == 2  # totals still count the old row


async def test_summary_dedupes_a_fork_copy_rated_like_its_original(
    app_with_db: tuple[Any, Any], tmp_path: Any,
) -> None:
    """Fix M3: a fork copies earlier turns onto fresh runs; a thumbs on the
    copy and on the original is ONE answer, not two."""
    from paperhub.db.fork import fork_session

    _, conn = app_with_db
    sid = await _session(conn)
    r1 = await _run(conn, sid, "first")
    r2 = await _run(conn, sid, "second")
    await _fb(conn, r1, "explicit", "up", [], "2026-11-03 10:00:00")
    await _fb(conn, r1, "implicit", "down", ["factual"], "2026-11-03 10:00:00")
    await conn.commit()
    fork = await fork_session(conn, source_session_id=sid, fork_run_id=r2,
                              workspace_dir=tmp_path)
    async with conn.execute(
        "SELECT run_id FROM messages WHERE session_id = ? AND role = 'assistant'",
        (fork.new_session_id,),
    ) as cur:
        (copy_run,), = await cur.fetchall()
    await _fb(conn, copy_run, "explicit", "up", [], "2026-11-03 11:00:00")
    await _fb(conn, copy_run, "implicit", "down", ["factual"], "2026-11-03 11:00:00")
    await conn.commit()
    s = await build_summary(conn, now=NOW)
    assert s.kpis.answers_with_feedback == 1
    assert s.kpis.approval_rate == 1.0
    assert s.kpis.negatives_implicit == 1
    answer = next(st for st in s.stages if st.stage == "answer")
    assert answer.implicit == 1


async def test_summary_counts_a_fork_of_fork_answer_once(
    app_with_db: tuple[Any, Any], tmp_path: Any,
) -> None:
    """A fork-of-fork copy B and the intermediate fork's copy A are the same
    answer as the unrated original O: they count once, not twice."""
    from paperhub.db.fork import fork_session

    _, conn = app_with_db
    sid = await _session(conn)
    await _run(conn, sid, "first")
    r2 = await _run(conn, sid, "second")
    await conn.commit()
    f1 = await fork_session(conn, source_session_id=sid, fork_run_id=r2,
                            workspace_dir=tmp_path)
    a_run = (await (await conn.execute(
        "SELECT run_id FROM messages WHERE session_id = ? AND role = 'assistant'",
        (f1.new_session_id,))).fetchone())[0]
    f1_turn = await _run(conn, f1.new_session_id, "second again")
    await conn.commit()
    f2 = await fork_session(conn, source_session_id=f1.new_session_id,
                            fork_run_id=f1_turn, workspace_dir=tmp_path)
    b_run = (await (await conn.execute(
        "SELECT run_id FROM messages WHERE session_id = ? AND role = 'assistant'",
        (f2.new_session_id,))).fetchone())[0]
    assert a_run != b_run
    for r in (a_run, b_run):
        await _fb(conn, r, "explicit", "down", ["factual"], "2026-11-03 10:00:00")
    await conn.commit()
    s = await build_summary(conn, now=NOW)
    assert s.kpis.answers_with_feedback == 1
    assert s.kpis.negatives_explicit == 1
    assert next(st for st in s.stages if st.stage == "answer").explicit == 1
    from paperhub.feedback.repo import list_cases
    assert len(await list_cases(conn)) == 1
