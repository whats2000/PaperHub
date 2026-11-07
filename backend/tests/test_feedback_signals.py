"""Plan H2 Task 2: trace-signal reason pre-selection (``guess_reasons``).

Fixture runs mirror the recorded shapes in a real workspace DB: the router
step is ``agent='router', tool='classify'`` with ``{intent, reasoning, ...}``;
each paper_qa subagent step is ``agent='research', tool='paper_qa:subagent'``
with ``{chunks_read, chunks_read_ids, ...}``; the answer lives in the
assistant ``messages`` row keyed by ``run_id``.
"""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

from paperhub.feedback.signals import guess_reasons


async def _session(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def _run(
    conn: aiosqlite.Connection,
    steps: list[tuple[str, str, str, dict[str, Any] | None, str | None]],
    answer: str | None = None,
    *,
    routing: dict[str, Any] | None = None,
) -> int:
    """Insert a run with ``steps`` = [(agent, tool, status, result, error)]."""
    sid = await _session(conn)
    cur = await conn.execute(
        "INSERT INTO runs (session_id, routing_decision_json, status) "
        "VALUES (?, ?, 'ok')",
        (sid, json.dumps(routing) if routing is not None else None),
    )
    assert cur.lastrowid is not None
    run_id = int(cur.lastrowid)
    for i, (agent, tool, status, result, error) in enumerate(steps):
        await conn.execute(
            "INSERT INTO tool_calls (run_id, step_index, agent, tool, "
            "result_summary_json, latency_ms, status, error) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (run_id, i, agent, tool,
             json.dumps(result) if result is not None else None, status, error),
        )
    if answer is not None:
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (?, 'assistant', ?, ?)",
            (sid, answer, run_id),
        )
    await conn.commit()
    return run_id


def _router(intent: str, reasoning: str = "content question") -> tuple[
    str, str, str, dict[str, Any], None,
]:
    return ("router", "classify", "ok",
            {"intent": intent, "confidence": 0.95, "reasoning": reasoning,
             "resolved_query": "q", "response_language": "English"}, None)


def _sub(chunks_read: int) -> tuple[str, str, str, dict[str, Any], None]:
    return ("research", "paper_qa:subagent", "ok",
            {"reads_used": 1, "chunks_read": chunks_read,
             "chunks_read_ids": list(range(chunks_read)),
             "chunks_cited": 0, "chunks_cited_ids": []}, None)


_RESOLVE = ("research", "paper_qa:resolve", "ok", {"enabled": []}, None)
_FINAL = ("research", "paper_qa:finalize", "ok", {"length": 10}, None)


async def test_clean_paper_qa_run_guesses_nothing(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db, [_router("paper_qa"), _RESOLVE, _sub(3), _sub(0), _FINAL],
        "Latency drops 83% [chunk:85335] (p.2).",
    )
    assert await guess_reasons(migrated_db, run) == []


async def test_clean_chitchat_without_markers_guesses_nothing(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db,
        [_router("chitchat"), ("chitchat", "generate", "ok", {"length": 5}, None)],
        "Hello!",
    )
    assert await guess_reasons(migrated_db, run) == []


async def test_clarify_intent_is_wrong_route(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(migrated_db, [_router("clarify")], "Which paper do you mean?")
    assert await guess_reasons(migrated_db, run) == ["wrong_route"]


async def test_priority_tie_break_reasoning_is_wrong_route(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db,
        [_router("memory", "multi-intent; memory wins by priority")],
        "Saved.",
    )
    assert await guess_reasons(migrated_db, run) == ["wrong_route"]


async def test_chinese_priority_wording_is_wrong_route(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(migrated_db, [_router("slides", "多意圖，依優先順序選 slides")], "ok")
    assert await guess_reasons(migrated_db, run) == ["wrong_route"]


async def test_priority_needs_a_word_boundary(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(migrated_db, [_router("chitchat", "prioritys")], "hi")
    assert await guess_reasons(migrated_db, run) == []


async def test_router_falls_back_to_routing_decision_json(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db, [], "Which one?",
        routing={"intent": "clarify", "reasoning": "unresolved anaphora"},
    )
    assert await guess_reasons(migrated_db, run) == ["wrong_route"]


async def test_all_subagents_read_nothing_is_wrong_source(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db, [_router("paper_qa"), _RESOLVE, _sub(0), _sub(0), _FINAL],
        "GISC is ... [chunk:7, 23]",
    )
    assert await guess_reasons(migrated_db, run) == ["wrong_source"]


async def test_paper_qa_answer_without_marker_is_citation(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db, [_router("paper_qa"), _RESOLVE, _sub(2), _FINAL],
        "The paper says latency drops.",
    )
    assert await guess_reasons(migrated_db, run) == ["citation"]


async def test_marker_with_space_counts_as_citation(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db, [_router("paper_qa"), _sub(2)], "Yes [chunk: 12].",
    )
    assert await guess_reasons(migrated_db, run) == []


async def test_error_steps_map_to_their_stage_reason(
    migrated_db: aiosqlite.Connection,
) -> None:
    router_err = await _run(
        migrated_db, [("router", "classify", "error", None, "timeout")], None,
    )
    assert await guess_reasons(migrated_db, router_err) == ["wrong_route"]

    qa_err = await _run(
        migrated_db,
        [_router("paper_qa"), _sub(2),
         ("research", "paper_qa:finalize", "error", None, "boom")],
        "Answer [chunk:1]",
    )
    assert await guess_reasons(migrated_db, qa_err) == ["wrong_source"]

    other_err = await _run(
        migrated_db,
        [_router("slides"), ("report", "report:compile", "error", None, "latex")],
        "Deck failed.",
    )
    assert await guess_reasons(migrated_db, other_err) == ["factual"]


async def test_cancelled_step_is_not_an_error_signal(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db,
        [_router("paper_suggest"),
         ("research", "paper_search:parse", "error", None, "cancelled")],
        None,
    )
    assert await guess_reasons(migrated_db, run) == []


async def test_reasons_are_deduped_in_canonical_order(
    migrated_db: aiosqlite.Connection,
) -> None:
    run = await _run(
        migrated_db,
        [_router("paper_qa", "picked by priority"), _sub(0),
         ("research", "paper_qa:finalize", "error", None, "boom")],
        "No markers here.",
    )
    assert await guess_reasons(migrated_db, run) == [
        "wrong_route", "wrong_source", "citation",
    ]


async def test_unknown_run_guesses_nothing(
    migrated_db: aiosqlite.Connection,
) -> None:
    assert await guess_reasons(migrated_db, 9999) == []
