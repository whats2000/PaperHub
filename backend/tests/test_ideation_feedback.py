"""Plan H3 Task 7: ideation feedback summary + G1 export of idea outcomes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
from httpx import ASGITransport, AsyncClient

VARS = {"problem": "rerank", "tried": "", "cards_xml": "<cards/>",
        "rejected_xml": "", "mode": "problem"}


kept_states = ("saved", "trying", "worked", "failed")


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _doc(conn: aiosqlite.Connection, n: int) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path) VALUES (?, 'pdf_upload', ?, ?, '', '', '')",
        (f"pdf:{n}", f"{n:064d}", f"Doc {n}"))
    await conn.commit()
    return int(cur.lastrowid or 0)


async def _idea(
    conn: aiosqlite.Connection, doc: int, status: str, *, prev: str | None = None,
    reasons: list[str] | None = None,
) -> int:
    cur = await conn.execute(
        "INSERT INTO ideas (source_doc, source_section, title, status, prev_status, "
        "dismiss_reasons_json, ever_saved) VALUES (?, 'Method', 'T', ?, ?, ?, ?)",
        (doc, status, prev, json.dumps(reasons or []),
         int(status in kept_states or prev in kept_states)))
    await conn.commit()
    return int(cur.lastrowid or 0)


async def _propose_run(conn: aiosqlite.Connection, kept: list[int]) -> int:
    cur = await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    cur = await conn.execute(
        "INSERT INTO runs (session_id, status) VALUES (?, 'ok')", (cur.lastrowid,))
    rid = int(cur.lastrowid or 0)
    await conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, args_redacted_json, "
        "result_summary_json, latency_ms, status) "
        "VALUES (?, 0, 'ideation', 'ideation:propose', ?, ?, 1, 'ok')",
        (rid, json.dumps({**VARS, "problem_id": 1}), json.dumps({"kept_ids": kept})))
    await conn.commit()
    return rid


def _inbox(tmp_path: Path) -> list[dict[str, Any]]:
    p = tmp_path / "exports" / "g1-inbox.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


async def test_summary_rates_and_reason_counts(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    d = await _doc(conn, 1)
    await _idea(conn, d, "new")
    await _idea(conn, d, "saved")
    await _idea(conn, d, "worked")
    await _idea(conn, d, "failed")
    await _idea(conn, d, "dismissed", prev="saved", reasons=["not_novel", "other"])
    await _idea(conn, d, "dismissed", prev="new", reasons=["not_novel"])
    async with _client(app) as c:
        r = await c.get("/ideation/feedback-summary")
    body = r.json()
    assert body["save_rate"] == 4 / 6  # saved, worked, failed, dismissed-from-saved
    assert body["worked_rate"] == 0.5
    assert body["dismiss_reasons"] == [
        {"reason": "not_novel", "count": 2}, {"reason": "other", "count": 1}]


async def test_summary_empty_library_is_zero_save_rate_and_null_worked_rate(app_with_db: tuple[Any, Any]) -> None:
    app, _ = app_with_db
    async with _client(app) as c:
        body = (await c.get("/ideation/feedback-summary")).json()
    assert body == {"save_rate": 0.0, "worked_rate": None, "dismiss_reasons": []}


async def test_worked_rate_null_without_outcomes(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    d = await _doc(conn, 1)
    await _idea(conn, d, "saved")
    async with _client(app) as c:
        body = (await c.get("/ideation/feedback-summary")).json()
    assert body["worked_rate"] is None and body["save_rate"] == 1.0


async def test_to_g1_expect_shape_and_idempotent(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    app, conn = app_with_db
    d = await _doc(conn, 1)
    saved = await _idea(conn, d, "saved")
    worked = await _idea(conn, d, "worked")
    dismissed = await _idea(conn, d, "dismissed", prev="new", reasons=["not_novel"])
    failed = await _idea(conn, d, "failed")
    await _idea(conn, d, "new")
    await _idea(conn, d, "trying")
    rid = await _propose_run(conn, [saved, worked, dismissed])
    async with _client(app) as c:
        r = await c.post("/ideation/feedback-to-g1")
        assert r.json() == {"appended": 4}
        assert (await c.post("/ideation/feedback-to-g1")).json() == {"appended": 0}
    lines = {ln["case_id"]: ln for ln in _inbox(tmp_path)}
    assert set(lines) == {
        f"idea{saved}-saved", f"idea{worked}-worked",
        f"idea{dismissed}-dismissed", f"idea{failed}-failed"}
    s = lines[f"idea{saved}-saved"]
    assert s["stage_hint"] == "ideation" and s["expect"] == {"source_doc": d}
    assert s["variables"] == {**VARS, "problem_id": 1} and s["source_run_id"] == rid
    assert s["observed"]["id"] == saved and s["observed"]["source_doc"] == d
    assert lines[f"idea{worked}-worked"]["expect"] == {"source_doc": d}
    n = lines[f"idea{dismissed}-dismissed"]
    assert n["expect"] == {} and n["negative_reasons"] == ["not_novel"]
    f = lines[f"idea{failed}-failed"]  # no producing run recorded
    assert f["expect"] == {} and f["variables"] == {} and f["source_run_id"] is None


async def test_ideation_lines_import_into_g1(
    app_with_db: tuple[Any, Any], tmp_path: Path,
) -> None:
    from benchmark.agent.corpus import import_inbox

    app, conn = app_with_db
    d = await _doc(conn, 1)
    i = await _idea(conn, d, "saved")
    j = await _idea(conn, d, "dismissed", reasons=["other"])
    await _propose_run(conn, [i, j])
    async with _client(app) as c:
        await c.post("/ideation/feedback-to-g1")
    cases, unlabelled, invalid = import_inbox(
        tmp_path / "exports" / "g1-inbox.jsonl", "ideation")
    assert [c.case_id for c in cases] == [f"idea{i}-saved"]
    assert cases[0].variables == {**VARS, "existing_xml": "", "response_language": ""}
    assert cases[0].expect == {"source_doc": d}
    assert unlabelled == 1 and invalid == 0  # the negative awaits a human label


async def test_save_rate_is_sticky_through_saved_new_dismissed(
    app_with_db: tuple[Any, Any],
) -> None:
    app, conn = app_with_db
    d = await _doc(conn, 1)
    iid = await _idea(conn, d, "new")
    await _idea(conn, d, "new")
    async with _client(app) as c:
        assert (await c.get("/ideation/feedback-summary")).json()["save_rate"] == 0.0
        await c.patch(f"/ideation/ideas/{iid}", json={"status": "saved"})
        await c.patch(f"/ideation/ideas/{iid}", json={"status": "new"})
        r = await c.patch(f"/ideation/ideas/{iid}", json={
            "status": "dismissed", "dismiss_reasons": ["other"]})
        assert r.status_code == 200 and r.json()["prev_status"] == "new"
        assert (await c.get("/ideation/feedback-summary")).json()["save_rate"] == 0.5


async def test_ever_saved_migration_backfills_old_dbs(tmp_path: Path) -> None:
    from paperhub.db.migrate import apply_schema

    async with aiosqlite.connect(tmp_path / "old.db") as conn:
        await apply_schema(conn)
        d = await _doc(conn, 1)
        a = await _idea(conn, d, "dismissed", prev="saved")
        b = await _idea(conn, d, "new")
        await conn.execute("UPDATE ideas SET ever_saved = 0")
        await conn.execute("ALTER TABLE ideas DROP COLUMN ever_saved")
        await conn.commit()
        await apply_schema(conn)  # re-adds the column and backfills
        await apply_schema(conn)  # idempotent
        async with conn.execute("SELECT id, ever_saved FROM ideas ORDER BY id") as cur:
            assert [tuple(r) for r in await cur.fetchall()] == [(a, 1), (b, 0)]
