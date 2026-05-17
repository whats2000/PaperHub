"""Tests for the Tool-Call Tracer (FR-11)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from paperhub.data.db import apply_migrations, connect
from paperhub.tracing.tracer import ToolCallTracer


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "paperhub.db"
    apply_migrations(p)
    return p


def _insert_run(db_path: Path, run_id_str: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO runs(id, session_id, routing_decision_json, started_at, status) "
            "VALUES (?, NULL, NULL, CURRENT_TIMESTAMP, 'running')",
            (run_id_str,),
        )


def test_record_step_commits_a_tool_calls_row(db_path: Path) -> None:
    run_id = uuid4()
    _insert_run(db_path, str(run_id))
    tracer = ToolCallTracer(db_path=db_path)
    tracer.record(
        run_id=run_id,
        step_index=0,
        parent_step=None,
        agent="router",
        tool="llm",
        model="claude-haiku-4-5",
        args={"prompt": "hi"},
        result_summary={"intent": "paper_qa"},
        latency_ms=42,
        token_in=10,
        token_out=2,
        status="ok",
        error=None,
    )
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT agent, tool, args_redacted_json, result_summary_json, status "
            "FROM tool_calls WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
    assert row["agent"] == "router"
    assert row["tool"] == "llm"
    assert row["status"] == "ok"
    assert json.loads(row["args_redacted_json"]) == {"prompt": "hi"}
    assert json.loads(row["result_summary_json"]) == {"intent": "paper_qa"}


def test_record_redacts_api_keys(db_path: Path) -> None:
    run_id = uuid4()
    _insert_run(db_path, str(run_id))
    tracer = ToolCallTracer(db_path=db_path)
    tracer.record(
        run_id=run_id,
        step_index=0,
        parent_step=None,
        agent="router",
        tool="llm",
        model="claude-haiku-4-5",
        args={"prompt": "sk-ant-AAAAA1234567890 hello"},
        result_summary=None,
        latency_ms=1,
        token_in=None,
        token_out=None,
        status="ok",
        error=None,
    )
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT args_redacted_json FROM tool_calls WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
    args = json.loads(row["args_redacted_json"])
    assert "sk-ant-AAAAA1234567890" not in args["prompt"]
    assert "<REDACTED:api-key>" in args["prompt"]


def test_record_step_enforces_unique_step_index(db_path: Path) -> None:
    import sqlite3

    run_id = uuid4()
    _insert_run(db_path, str(run_id))
    tracer = ToolCallTracer(db_path=db_path)
    tracer.record(
        run_id=run_id,
        step_index=0,
        parent_step=None,
        agent="router",
        tool="llm",
        model=None,
        args={},
        result_summary=None,
        latency_ms=1,
        token_in=None,
        token_out=None,
        status="ok",
        error=None,
    )
    with pytest.raises(sqlite3.IntegrityError):
        tracer.record(
            run_id=run_id,
            step_index=0,
            parent_step=None,
            agent="router",
            tool="llm",
            model=None,
            args={},
            result_summary=None,
            latency_ms=1,
            token_in=None,
            token_out=None,
            status="ok",
            error=None,
        )
