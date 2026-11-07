"""Plan H2 Task 4: the background ``feedback:review`` run and its two hooks."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.feedback import review
from paperhub.feedback.models import ReviewVerdict, format_reasons_block

_ROUTER_CHITCHAT = '{"intent":"chitchat","confidence":0.9,"reasoning":"chat"}'


class _StubLlm:
    def __init__(self, verdict: ReviewVerdict | None = None,
                 exc: Exception | None = None) -> None:
        self.verdict = verdict
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.verdict

    def stream(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


async def _drain() -> None:
    while review._live_tasks:
        await asyncio.gather(*list(review._live_tasks))


class _FakeMcpRegistry:
    async def aggregate_tool_schemas(self) -> list[Any]:
        return []

    async def has_tool(self, name: str) -> bool:
        return False


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_FEEDBACK_REVIEW", "1")
    monkeypatch.delenv("PAPERHUB_FEEDBACK_KEYWORDS", raising=False)
    monkeypatch.setenv("PAPERHUB_ROUTER_MOCK", _ROUTER_CHITCHAT)
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", "Sure.")
    return tmp_path


@pytest.fixture
def scheduled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(review, "schedule_review", lambda **kw: calls.append(kw))
    return calls


async def _client(db: Path) -> AsyncClient:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
    app = create_app()
    app.state.mcp_registry = _FakeMcpRegistry()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _chat(client: AsyncClient, message: str, session_id: int | None) -> int:
    body: dict[str, Any] = {"user_message": message}
    if session_id is not None:
        body["session_id"] = session_id
    sid = session_id
    async with client.stream("POST", "/chat", json=body) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: ") and sid is None and '"session_id"' in line:
                sid = int(json.loads(line[6:])["session_id"])
    assert sid is not None
    return sid


async def _fetchall(db: Path, sql: str) -> list[tuple[Any, ...]]:
    async with aiosqlite.connect(db) as conn, conn.execute(sql) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def _runs(db: Path) -> list[tuple[int, str]]:
    rows = await _fetchall(db, "SELECT id, status FROM runs ORDER BY id")
    return [(int(r[0]), str(r[1])) for r in rows]


# ------------------------------------------------------------ keyword hook


async def test_keyword_message_schedules_review_of_previous_answer(
    workspace: Path, scheduled: list[dict[str, Any]],
) -> None:
    db = workspace / "paperhub.db"
    async with await _client(db) as client:
        sid = await _chat(client, "what is attention?", None)
        assert scheduled == []  # first turn: matches nothing
        await _chat(client, "that's wrong, I asked about the decoder", sid)
    runs = await _runs(db)
    assert len(scheduled) == 1
    call = scheduled[0]
    assert call["target_run_id"] == runs[0][0]
    assert call["session_id"] == sid
    assert call["trigger"] == "keyword"
    assert call["followup"] == "that's wrong, I asked about the decoder"


async def test_non_matching_message_schedules_nothing(
    workspace: Path, scheduled: list[dict[str, Any]],
) -> None:
    db = workspace / "paperhub.db"
    async with await _client(db) as client:
        sid = await _chat(client, "what is attention?", None)
        await _chat(client, "thanks, and the encoder?", sid)
    assert scheduled == []


async def test_keyword_with_no_previous_answer_schedules_nothing(
    workspace: Path, scheduled: list[dict[str, Any]],
) -> None:
    async with await _client(workspace / "paperhub.db") as client:
        await _chat(client, "is it wrong to use CEM here?", None)
    assert scheduled == []


async def test_setting_off_disables_both_hooks(
    workspace: Path, scheduled: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_FEEDBACK_REVIEW", "0")
    db = workspace / "paperhub.db"
    async with await _client(db) as client:
        sid = await _chat(client, "what is attention?", None)
        await _chat(client, "wrong", sid)
        runs = await _runs(db)
        resp = await client.post(f"/sessions/{sid}/fork", json={"run_id": runs[0][0]})
        assert resp.status_code == 201
    assert scheduled == []


async def test_llm_failure_records_error_run_and_leaves_turn_ok(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = review.schedule_review
    failing = _StubLlm(exc=RuntimeError("provider down"))

    def _schedule(**kw: Any) -> None:
        real(**{**kw, "llm": failing})

    monkeypatch.setattr(review, "schedule_review", _schedule)
    db = workspace / "paperhub.db"
    async with await _client(db) as client:
        sid = await _chat(client, "what is attention?", None)
        await _chat(client, "incorrect", sid)
        await _drain()
    assert len(failing.calls) == 1
    async with aiosqlite.connect(db) as conn:
        async with conn.execute(
            "SELECT r.id, r.status, "
            "(SELECT COUNT(*) FROM messages m WHERE m.run_id = r.id) "
            "FROM runs r ORDER BY r.id",
        ) as cur:
            rows = await cur.fetchall()
        # turn 1, turn 2 (both ok with their messages), then the review run.
        assert [(r[1], r[2]) for r in rows] == [("ok", 2), ("ok", 2), ("error", 0)]
        async with conn.execute(
            "SELECT agent, tool, status, error FROM tool_calls WHERE run_id = ?",
            (rows[2][0],),
        ) as cur:
            steps = await cur.fetchall()
        assert steps == [("feedback", "feedback:review", "error", "provider down")]
        async with conn.execute("SELECT COUNT(*) FROM feedback") as cur:
            assert (await cur.fetchone())[0] == 0


# ------------------------------------------------------------ review task


async def _seed_target(db: Path) -> int:
    """A paper_qa turn on P1 (of P1, P2) that cites chunk 1, then returns its
    run id."""
    async with aiosqlite.connect(db) as conn:
        # WAL up front, as in the real workspace: two reviews opening a fresh
        # rollback-journal DB at once race on switching it ("database is locked").
        await conn.execute("PRAGMA journal_mode = WAL")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('S')")
        for key, title in (("a", "Attention Is All You Need"), ("b", "BERT")):
            await conn.execute(
                "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, "
                "source_dir_path, html_path) VALUES (?, 'arxiv', ?, ?, '/x', '/x', '/x')",
                (key, key, title),
            )
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id) VALUES (1, 1), (1, 2)",
        )
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (1, '5.1 Training Data', 0, 10, 'We trained on WMT 2014 English-German.')",
        )
        cur = await conn.execute(
            "INSERT INTO runs (session_id, status, routing_decision_json) "
            "VALUES (1, 'ok', ?)", (json.dumps({"intent": "paper_qa"}),),
        )
        run_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'Which datasets does the second paper use?', ?)",
            (run_id,),
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'assistant', 'It uses WMT 2014 [chunk:1].', ?)", (run_id,),
        )
        steps = [
            ("router", "classify", None, {"intent": "paper_qa", "reasoning": "qa"}),
            ("research", "paper_qa:resolve", None,
             {"enabled": [{"id": 1, "title": "Attention Is All You Need"},
                          {"id": 2, "title": "BERT"}]}),
            ("research", "paper_qa:subagent", {"paper_content_id": 1},
             {"chunks_read": 1, "tool_call_log": [
                 {"tool": "list_sections", "args": {}},
                 {"tool": "read_section", "args": {"name": "5.1 Training Data"},
                  "chunk_ids_returned": [1]},
             ]}),
        ]
        for i, (agent, tool, args, result) in enumerate(steps):
            await conn.execute(
                "INSERT INTO tool_calls (run_id, branch, step_index, agent, tool, "
                "args_redacted_json, result_summary_json, latency_ms, status) "
                "VALUES (?, '', ?, ?, ?, ?, ?, 1, 'ok')",
                (run_id, i, agent, tool, json.dumps(args) if args else None,
                 json.dumps(result)),
            )
        await conn.commit()
    return run_id


async def _run(db: Path, run_id: int, llm: _StubLlm,
               trigger: str = "keyword", followup: str | None = "wrong paper") -> None:
    review.schedule_review(
        db_path=db, session_id=1, target_run_id=run_id, followup=followup,
        trigger=trigger,  # type: ignore[arg-type]
        llm=llm, model="stub-model",
    )
    await _drain()


async def test_uncertain_verdict_needs_confirmation(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    llm = _StubLlm(ReviewVerdict(verdict="uncertain", reasons=["wrong_source"],
                                 doubt="Which part looks wrong to you?"))
    await _run(db, run_id, llm)

    v = llm.calls[0]
    assert v["slot"] == "feedback_review/v1"
    assert v["model"] == "stub-model"
    assert v["variables"]["question"] == "Which datasets does the second paper use?"
    assert v["variables"]["answer"] == "It uses WMT 2014 [chunk:1]."
    assert v["variables"]["user_followup"] == "wrong paper"
    assert v["variables"]["reasons"] == format_reasons_block()
    assert v["variables"]["trace_summary"] == (
        'route: paper_qa (refs: [P1] "Attention Is All You Need"; [P2] "BERT")\n'
        "read: P1 §5.1 Training Data\n"
        'cited: chunk 1 (P1 §5.1) "We trained on WMT 2014 English-German."\n'
        "not read: P2"
    )

    async with aiosqlite.connect(db) as conn:
        async with conn.execute(
            "SELECT run_id, message_id, kind, polarity, reasons_json, trigger, "
            "verdict, status, reviewer_rationale FROM feedback",
        ) as cur:
            rows = await cur.fetchall()
        assert rows == [(run_id, 2, "implicit", "down", '["wrong_source"]', "keyword",
                         "uncertain", "needs_confirmation",
                         "Which part looks wrong to you?")]
        async with conn.execute(
            "SELECT id, session_id, status FROM runs WHERE id > ?", (run_id,),
        ) as cur:
            (review_run, sid, status), = await cur.fetchall()
        assert (sid, status) == (1, "ok")
        async with conn.execute(
            "SELECT agent, tool, model, status, args_redacted_json, result_summary_json "
            "FROM tool_calls WHERE run_id = ?", (review_run,),
        ) as cur:
            (agent, tool, model, status, args, result), = await cur.fetchall()
        async with conn.execute(
            "SELECT step_index FROM tool_calls WHERE run_id = ? ORDER BY step_index",
            (run_id,),
        ) as cur:
            target_steps = [int(r[0]) for r in await cur.fetchall()]
        async with conn.execute("SELECT COUNT(*) FROM messages WHERE run_id = ?",
                                (review_run,)) as cur:
            assert (await cur.fetchone())[0] == 0
    assert (agent, tool, model, status) == ("feedback", "feedback:review", "stub-model", "ok")
    assert json.loads(args) == {
        "target_run_id": run_id, "trigger": "keyword", "answer_message_id": 2,
        "question": v["variables"]["question"], "answer": v["variables"]["answer"],
        "user_followup": "wrong paper",
        "trace_summary": v["variables"]["trace_summary"],
    }
    res = json.loads(result)
    assert res["verdict"] == "uncertain"
    assert res["reasons"] == ["wrong_source"]
    assert res["doubt"] == "Which part looks wrong to you?"
    assert res["step_ids_read"] == target_steps
    assert isinstance(res["guessed_reasons"], list)
    assert "prompt" not in res and "messages" not in res
    assert res["status"] == "needs_confirmation"
    assert res["feedback_id"] == (await _fetchall(db, "SELECT id FROM feedback"))[0][0]


async def test_rejected_verdict_is_stored_final(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    await _run(db, run_id, _StubLlm(ReviewVerdict(
        verdict="rejected", reasons=[], doubt="You are asking a new question.")))
    async with aiosqlite.connect(db) as conn, conn.execute(
        "SELECT kind, verdict, status, reviewer_rationale FROM feedback",
    ) as cur:
        assert await cur.fetchall() == [
            ("implicit", "rejected", "final", "You are asking a new question.")]


async def test_confirmed_verdict_is_final(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    await _run(db, run_id, _StubLlm(ReviewVerdict(
        verdict="confirmed", reasons=["wrong_source"], doubt="It answered about P1.")))
    assert await _fetchall(db, "SELECT verdict, status FROM feedback") == [
        ("confirmed", "final")]


async def test_fork_review_has_empty_followup_and_dedupes(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    llm = _StubLlm(ReviewVerdict(verdict="uncertain", reasons=[], doubt="?"))
    await _run(db, run_id, llm, trigger="fork", followup=None)
    await _run(db, run_id, llm, trigger="fork", followup=None)  # same run again
    assert len(llm.calls) == 1
    assert llm.calls[0]["variables"]["user_followup"] == ""
    assert await _fetchall(db, "SELECT trigger FROM feedback") == [("fork",)]


async def test_fork_review_always_needs_confirmation(tmp_path: Path) -> None:
    """Ruling K2: the reviewer was never evaluated on forks, so even a
    confirmed fork verdict waits for the user; the verdict is kept as the guess."""
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    await _run(db, run_id, _StubLlm(ReviewVerdict(
        verdict="confirmed", reasons=["factual"], doubt="T is 1000.")),
        trigger="fork", followup=None)
    assert await _fetchall(
        db, "SELECT trigger, verdict, status, reasons_json, reviewer_rationale "
            "FROM feedback",
    ) == [("fork", "confirmed", "needs_confirmation", '["factual"]', "T is 1000.")]


class _BarrierLlm(_StubLlm):
    """Holds every call until ``n`` have arrived, so concurrent reviews all pass
    the pre-check before any of them inserts."""

    def __init__(self, verdict: ReviewVerdict, n: int) -> None:
        super().__init__(verdict)
        self._n = n
        self._all_in = asyncio.Event()

    async def structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if len(self.calls) >= self._n:
            self._all_in.set()
        await asyncio.wait_for(self._all_in.wait(), timeout=10)
        return self.verdict


async def test_concurrent_reviews_of_one_run_store_one_row(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    llm = _BarrierLlm(ReviewVerdict(verdict="rejected", reasons=[], doubt="x"), 2)
    for _ in range(2):
        review.schedule_review(
            db_path=db, session_id=1, target_run_id=run_id, followup="wrong",
            trigger="keyword", llm=llm, model="m",
        )
    await _drain()
    assert len(llm.calls) == 2
    assert await _fetchall(db, "SELECT COUNT(*) FROM feedback") == [(1,)]
    results = [
        json.loads(r[0]) for r in await _fetchall(
            db, "SELECT result_summary_json FROM tool_calls "
                "WHERE tool = 'feedback:review' ORDER BY run_id",
        )
    ]
    assert sorted(r.get("skipped", "") for r in results) == ["", "duplicate"]
    stored = (await _fetchall(db, "SELECT id FROM feedback"))[0][0]
    assert sorted((r["feedback_id"] is None, r["feedback_id"]) for r in results) == [
        (False, stored), (True, None)]


@pytest.mark.parametrize("change", [
    "UPDATE messages SET content = '   ' WHERE role = 'assistant'",
    "UPDATE runs SET status = 'error' WHERE id = 1",
])
async def test_blank_answer_or_failed_run_is_skipped(tmp_path: Path, change: str) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    async with aiosqlite.connect(db) as conn:
        await conn.execute(change)
        await conn.commit()
    llm = _StubLlm(ReviewVerdict(verdict="confirmed", reasons=[], doubt="x"))
    await _run(db, run_id, llm)
    assert llm.calls == []
    assert await _fetchall(db, "SELECT COUNT(*) FROM feedback") == [(0,)]
    (result,), = await _fetchall(
        db, "SELECT result_summary_json FROM tool_calls WHERE tool = 'feedback:review'",
    )
    assert "skipped" in json.loads(result)
    assert (await _runs(db))[-1][1] == "ok"


async def test_migration_dedupes_implicit_rows_before_the_index(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    async with aiosqlite.connect(db) as conn:
        await conn.execute("DROP INDEX idx_feedback_implicit_run_trigger")
        for doubt in ("old", "new"):
            await conn.execute(
                "INSERT INTO feedback (run_id, kind, polarity, trigger, verdict, "
                "status, reviewer_rationale) VALUES (?, 'implicit', 'down', "
                "'keyword', 'rejected', 'final', ?)", (run_id, doubt),
            )
        await conn.execute(
            "INSERT INTO feedback (run_id, kind, polarity, trigger, verdict, status) "
            "VALUES (?, 'implicit', 'down', 'fork', 'uncertain', 'needs_confirmation')",
            (run_id,),
        )
        await conn.commit()
        await apply_schema(conn)
        await apply_schema(conn)  # idempotent
    assert await _fetchall(
        db, "SELECT trigger, reviewer_rationale FROM feedback ORDER BY id",
    ) == [("keyword", "new"), ("fork", None)]
    assert await _fetchall(
        db, "SELECT name FROM sqlite_master WHERE name = 'idx_feedback_implicit_run_trigger'",
    ) == [("idx_feedback_implicit_run_trigger",)]


async def test_run_without_answer_is_skipped(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    async with aiosqlite.connect(db) as conn:
        await conn.execute("DELETE FROM messages WHERE role = 'assistant'")
        await conn.commit()
    llm = _StubLlm(ReviewVerdict(verdict="confirmed", reasons=[], doubt="x"))
    await _run(db, run_id, llm)
    assert llm.calls == []
    assert await _fetchall(db, "SELECT COUNT(*) FROM feedback") == [(0,)]


# ------------------------------------------------------------ fork hook


async def test_fork_endpoint_schedules_fork_review(
    workspace: Path, scheduled: list[dict[str, Any]],
) -> None:
    db = workspace / "paperhub.db"
    async with await _client(db) as client:
        sid = await _chat(client, "first", None)
        await _chat(client, "second", sid)
        runs = await _runs(db)
        resp = await client.post(f"/sessions/{sid}/fork", json={"run_id": runs[1][0]})
        assert resp.status_code == 201
        new_sid = resp.json()["session_id"]
    assert len(scheduled) == 1
    call = scheduled[0]
    assert (call["session_id"], call["target_run_id"], call["trigger"],
            call["followup"]) == (sid, runs[1][0], "fork", None)
    async with aiosqlite.connect(db) as conn, conn.execute(
        "SELECT forked_from_run_id FROM chat_sessions WHERE id = ?", (new_sid,),
    ) as cur:
        assert (await cur.fetchone())[0] == runs[1][0]


async def test_repeated_chunk_prefix_marker_is_cited(tmp_path: Path) -> None:
    """Fix C1: ``[chunk:1, chunk:2]`` cites both chunks."""
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "UPDATE messages SET content = 'WMT [chunk:1, chunk:7].' "
            "WHERE role = 'assistant'",
        )
        await conn.commit()
        inputs = await review.load_review_inputs(conn, run_id)
    assert inputs is not None
    assert inputs.cited_chunk_ids == [1, 7]
    assert "cited: chunk 7 (not found)" in inputs.trace_summary


async def test_review_step_is_harvestable_for_the_eval_stage(tmp_path: Path) -> None:
    """Fix I2: the recorded step input reconstructs the reviewer's variables,
    so ``harvest --stage feedback_review`` yields the exact LLM input."""
    from benchmark.agent.corpus import harvest

    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    llm = _StubLlm(ReviewVerdict(verdict="confirmed", reasons=["wrong_source"],
                                 doubt="d"))
    await _run(db, run_id, llm)
    (case,) = harvest(db, "feedback_review")
    assert case.variables == llm.calls[0]["variables"]
    assert all(case.variables[k] for k in ("question", "answer", "user_followup",
                                           "trace_summary", "reasons"))


async def test_keyword_after_fork_skips_a_copied_answer(
    workspace: Path, scheduled: list[dict[str, Any]],
) -> None:
    """Fix M3: the answer right before a fork's first new message is a COPY of
    an earlier answer, already reviewable in its own session; skip it."""
    db = workspace / "paperhub.db"
    async with await _client(db) as client:
        sid = await _chat(client, "first", None)
        await _chat(client, "second", sid)
        runs = await _runs(db)
        resp = await client.post(f"/sessions/{sid}/fork", json={"run_id": runs[1][0]})
        new_sid = resp.json()["session_id"]
        scheduled.clear()  # the fork's own review
        await _chat(client, "wrong, try again", new_sid)
        assert scheduled == []
        await _chat(client, "that's not it", new_sid)  # a NEW answer: reviewed
    assert len(scheduled) == 1 and scheduled[0]["trigger"] == "keyword"


# ------------------------------------------------------------ bounded reviewer call

_LOOP = '{\n  "verdict": "confirmed",\n  "doubt": "' + "\u0300" * 2000


class _SeqLlm:
    """Each call takes the next behaviour: a raw JSON string (validated like the
    adapter does), or ``"sleep"`` (never answers)."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    async def structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        out = self.outputs[min(len(self.calls), len(self.outputs)) - 1]
        if out == "sleep":
            await asyncio.sleep(30)
        return kwargs["response_model"].model_validate_json(out)

    def stream(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


async def _review_step(db: Path, run_id: int) -> tuple[str, str, str | None, Any]:
    rows = await _fetchall(
        db, "SELECT r.status, t.status, t.error, t.result_summary_json FROM runs r "
            f"JOIN tool_calls t ON t.run_id = r.id WHERE r.id > {run_id}")
    (run_status, step_status, error, result), = rows
    return run_status, step_status, error, json.loads(result) if result else None


async def test_invalid_json_is_retried_once_then_stored(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    good = '{"verdict": "confirmed", "reasons": ["factual"], "doubt": "d"}'
    llm = _SeqLlm(_LOOP, good)
    await _run(db, run_id, llm)
    assert len(llm.calls) == 2
    assert all(c["max_tokens"] == review.REVIEW_MAX_TOKENS for c in llm.calls)
    run_status, step_status, _, result = await _review_step(db, run_id)
    assert (run_status, step_status) == ("ok", "ok")
    assert result["attempts"] == 2 and result["verdict"] == "confirmed"
    assert await _fetchall(db, "SELECT verdict FROM feedback") == [("confirmed",)]


async def test_invalid_json_twice_records_the_failure(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    llm = _SeqLlm(_LOOP)
    await _run(db, run_id, llm)
    assert len(llm.calls) == review.REVIEW_ATTEMPTS == 2
    run_status, step_status, error, result = await _review_step(db, run_id)
    assert (run_status, step_status) == ("error", "error")
    assert error == "invalid_json after 2 attempt(s)"
    assert result["failure"] == "invalid_json" and result["attempts"] == 2
    assert result["output_head"] == _LOOP[:300]
    assert "prompt" not in result and "messages" not in result
    assert await _fetchall(db, "SELECT COUNT(*) FROM feedback") == [(0,)]


async def test_invalid_verdict_kind(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    await _run(db, run_id, _SeqLlm('{"verdict": "maybe", "reasons": [], "doubt": ""}'))
    _, _, _, result = await _review_step(db, run_id)
    assert result["failure"] == "invalid_verdict"
    assert "maybe" in result["output_head"]


async def test_review_timeout_is_bounded_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review, "REVIEW_TIMEOUT_S", 0.05)
    db = tmp_path / "t.db"
    run_id = await _seed_target(db)
    llm = _SeqLlm("sleep")
    await asyncio.wait_for(_run(db, run_id, llm), timeout=10)
    run_status, step_status, error, result = await _review_step(db, run_id)
    assert (run_status, step_status) == ("error", "error")
    assert error == "timeout after 1 attempt(s)"
    assert result == {"failure": "timeout", "attempts": 1, "output_head": None}
    assert await _fetchall(db, "SELECT COUNT(*) FROM feedback") == [(0,)]
