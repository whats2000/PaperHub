"""Plan H2 Task 8: POST /feedback/g1-inbox."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

ARGS = {"user_message": "show me my papers", "enabled_refs_count": 2,
        "slide_attached": False}


async def _seed(conn: aiosqlite.Connection, reasons: list[str],
                with_router: bool = True) -> tuple[int, int]:
    cur = await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    sid = int(cur.lastrowid or 0)
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'ok')", (sid,))
    rid = int(cur.lastrowid or 0)
    if with_router:
        await conn.execute(
            "INSERT INTO tool_calls (run_id, step_index, agent, tool, "
            "args_redacted_json, result_summary_json, latency_ms, status) "
            "VALUES (?, 0, 'router', 'classify', ?, ?, 1, 'ok')",
            (rid, json.dumps(ARGS), json.dumps({"intent": "paper_qa"})))
    cur = await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, reasons_json, correction) "
        "VALUES (?, 'explicit', 'down', ?, 'should be library_stats')",
        (rid, json.dumps(reasons)))
    await conn.commit()
    return int(cur.lastrowid or 0), rid


def _inbox(tmp_path: Path) -> list[dict[str, Any]]:
    p = tmp_path / "exports" / "g1-inbox.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


async def test_inbox_line_carries_router_state_and_is_idempotent(
    app_with_db: tuple[Any, aiosqlite.Connection], tmp_path: Path,
) -> None:
    app, conn = app_with_db
    fid, rid = await _seed(conn, ["wrong_route", "factual"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/g1-inbox", json={"feedback_ids": [fid]})
        assert r.status_code == 200 and r.json() == {"appended": 1}
        r = await c.post("/feedback/g1-inbox", json={"feedback_ids": [fid, fid]})
        assert r.json() == {"appended": 0}
    assert _inbox(tmp_path) == [{
        "case_id": f"fb{fid}-run{rid}", "stage_hint": "router",
        "reasons": ["wrong_route", "factual"],
        "correction": "should be library_stats", "source_run_id": rid,
        "variables": ARGS, "observed": {"intent": "paper_qa"}, "expect": {},
    }]


async def test_unknown_id_is_404_and_writes_nothing(
    app_with_db: tuple[Any, aiosqlite.Connection], tmp_path: Path,
) -> None:
    app, conn = app_with_db
    fid, _ = await _seed(conn, ["citation"], with_router=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/g1-inbox", json={"feedback_ids": [fid, 9999]})
        assert r.status_code == 404
        assert not (tmp_path / "exports" / "g1-inbox.jsonl").exists()
        # a run with no router step still bridges, with empty recorded state
        r = await c.post("/feedback/g1-inbox", json={"feedback_ids": [fid]})
        assert r.json() == {"appended": 1}
    line = _inbox(tmp_path)[0]
    assert line["stage_hint"] == "citation" and line["variables"] == {}


async def test_concurrent_appends_keep_one_line(
    app_with_db: tuple[Any, aiosqlite.Connection], tmp_path: Path,
) -> None:
    app, conn = app_with_db
    fid, _ = await _seed(conn, ["wrong_route"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        rs = await asyncio.gather(*[
            c.post("/feedback/g1-inbox", json={"feedback_ids": [fid]})
            for _ in range(8)])
    assert sum(r.json()["appended"] for r in rs) == 1
    assert len(_inbox(tmp_path)) == 1


async def test_corrupt_or_empty_reasons_give_null_hint(
    app_with_db: tuple[Any, aiosqlite.Connection], tmp_path: Path,
) -> None:
    app, conn = app_with_db
    fid, _ = await _seed(conn, [])
    fid2, _ = await _seed(conn, ["mystery"])
    fid3, _ = await _seed(conn, [])
    await conn.execute("UPDATE feedback SET reasons_json = 'not json' WHERE id = ?",
                       (fid3,))
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/feedback/g1-inbox", json={"feedback_ids": [fid, fid2, fid3]})
        assert r.status_code == 200 and r.json() == {"appended": 3}
    lines = _inbox(tmp_path)
    assert [ln["stage_hint"] for ln in lines] == [None, None, None]
    assert lines[2]["reasons"] == []


async def test_only_counted_negatives_bridge_all_or_nothing(
    app_with_db: tuple[Any, aiosqlite.Connection], tmp_path: Path,
) -> None:
    """Fix M5: explicit down or a confirmed final implicit flag; anything else
    (a thumbs-up, a pending / rejected / uncertain flag) is a 422 and nothing
    is written."""
    app, conn = app_with_db
    good, rid = await _seed(conn, ["wrong_route"])
    bad: list[int] = []
    for kind, polarity, status, verdict in (
        ("implicit", "down", "needs_confirmation", "uncertain"),
        ("implicit", "down", "final", "rejected"),
        ("implicit", "down", "final", "uncertain"),
    ):
        cur = await conn.execute(
            "INSERT INTO feedback (run_id, kind, polarity, trigger, status, verdict) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, kind, polarity, "keyword" if status == "final" else "fork",
             status, verdict),
        )
        bad.append(int(cur.lastrowid or 0))
        if len(bad) == 2:  # one implicit row per (run, trigger): new run
            cur = await conn.execute("INSERT INTO runs (session_id, status) "
                                     "VALUES (1, 'ok')")
            rid = int(cur.lastrowid or 0)
    up, _ = await _seed(conn, [])
    await conn.execute("UPDATE feedback SET polarity = 'up' WHERE id = ?", (up,))
    cur = await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, trigger, status, verdict) "
        "VALUES (?, 'implicit', 'down', 'fork', 'final', 'confirmed')", (rid,),
    )
    confirmed = int(cur.lastrowid or 0)
    await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for fid in [*bad, up]:
            r = await c.post("/feedback/g1-inbox", json={"feedback_ids": [good, fid]})
            assert r.status_code == 422, fid
        assert not (tmp_path / "exports" / "g1-inbox.jsonl").exists()
        r = await c.post("/feedback/g1-inbox", json={"feedback_ids": [good, confirmed]})
        assert r.json() == {"appended": 2}
