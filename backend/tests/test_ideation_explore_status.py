"""Plan H3 Task 5 (Ruling U3): ``GET /ideation/explore/status``."""
from __future__ import annotations

import json
from typing import Any

from httpx import ASGITransport, AsyncClient

from paperhub.ideation.agent import EXPLORE_MODE_JSON


async def test_explore_status_reports_the_latest_run(app_with_db: tuple[Any, Any]) -> None:
    app, conn = app_with_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        empty = (await c.get("/ideation/explore/status")).json()
        assert empty == {"running": False, "last_run_at": None,
                         "last_new_count": None, "last_error": None,
                         "cards_ready": None, "cards_total": None, "skipped_docs": 0,
                         "nothing_relevant": False, "nothing_relevant_reason": ""}

        await conn.execute("INSERT INTO chat_sessions (title, internal) VALUES ('i', 1)")
        cur = await conn.execute(
            "INSERT INTO runs (session_id, status, routing_decision_json, finished_at) "
            "VALUES (1, 'ok', ?, '2026-11-06 10:00:00')", (EXPLORE_MODE_JSON,))
        await conn.execute(
            "INSERT INTO tool_calls (run_id, branch, step_index, agent, tool, "
            "result_summary_json, status, latency_ms) VALUES (?, '', 0, 'ideation', "
            "'ideation:propose', ?, 'ok', 5)", (cur.lastrowid, json.dumps({"new_count": 0})))
        await conn.commit()
        body = (await c.get("/ideation/explore/status")).json()
    assert body == {"running": False, "last_run_at": "2026-11-06 10:00:00",
                    "last_new_count": 0, "last_error": None,
                    "cards_ready": None, "cards_total": None, "skipped_docs": 0,
                    "nothing_relevant": False, "nothing_relevant_reason": ""}
