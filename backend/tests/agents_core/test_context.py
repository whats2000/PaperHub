"""Tests for the core agent's precise-context builder (H4 Task 9)."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.context import (
    AGENT_KEEP_TURNS,
    ContextBlocks,
    build_conversation,
    build_session_state,
    load_conversation,
)

from .test_read_tools import _paper

pytestmark = pytest.mark.asyncio


async def _session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    await conn.commit()
    return int(row[0])


async def _turn(
    conn: aiosqlite.Connection, sid: int, question: str, answer: str,
    record: dict[str, Any] | None,
) -> int:
    await conn.execute(
        "INSERT INTO runs (session_id, agent_record_json) VALUES (?, ?)",
        (sid, json.dumps(record) if record is not None else None),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    rid = int(row[0])
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (?, 'user', ?, ?)",
        (sid, question, rid),
    )
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'assistant', ?, ?)",
        (sid, answer, rid),
    )
    await conn.commit()
    return rid


GISC_ANSWER = (
    "Port GISC's sparsity regulariser into YOLOSeg's loss as an extra L1 "
    "term on the mask logits [chunk:11]."
)


async def test_follow_up_carries_history(migrated_db: aiosqlite.Connection) -> None:
    """Review Focus 1: the earlier answer and its work record reach the model."""
    sid = await _session(migrated_db)
    await _turn(
        migrated_db, sid, "How could GISC sparsity port to YOLOSeg?", GISC_ANSWER,
        {"tools": ["read_section", "gather"], "read": [11, 12], "cited": [11],
         "papers": [1, 2], "steps": 3},
    )
    history = [
        {"role": "user", "content": "How could GISC sparsity port to YOLOSeg?"},
        {"role": "assistant", "content": GISC_ANSWER},
    ]
    msgs = await build_conversation(migrated_db, sid, history)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert GISC_ANSWER in msgs[1]["content"]
    assert "<work_record>" in msgs[1]["content"]
    assert "read_section" in msgs[1]["content"]
    assert "read: 11, 12" in msgs[1]["content"]
    assert "cited: 11" in msgs[1]["content"]
    conv = await load_conversation(migrated_db, sid, history)
    assert conv.kept_ids == {11, 12}


async def test_pipeline_turn_work_record_falls_back_to_answer_citations(
    migrated_db: aiosqlite.Connection,
) -> None:
    sid = await _session(migrated_db)
    await _turn(migrated_db, sid, "q", "Answer [chunk:40] and [chunk:41].", None)
    conv = await load_conversation(migrated_db, sid, [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "Answer [chunk:40] and [chunk:41]."},
    ])
    assert "cited: 40, 41" in conv.messages[1]["content"]
    assert conv.kept_ids == {40, 41}


async def test_old_turns_fold_into_summary(migrated_db: aiosqlite.Connection) -> None:
    sid = await _session(migrated_db)
    history: list[dict[str, Any]] = []
    for i in range(20):
        q, a = f"question {i}", f"answer {i} [chunk:{100 + i}]"
        await _turn(migrated_db, sid, q, a,
                    {"tools": ["read_chunks"], "read": [100 + i],
                     "cited": [100 + i], "papers": [], "steps": 1})
        history += [{"role": "user", "content": q},
                    {"role": "assistant", "content": a}]
    conv = await load_conversation(migrated_db, sid, history, keep_turns=AGENT_KEEP_TURNS)
    msgs = conv.messages
    summaries = [m for m in msgs if "<earlier_summary>" in m["content"]]
    assert len(summaries) == 1
    assert "question 0" in summaries[0]["content"]
    assert "question 7" in summaries[0]["content"]
    assert "cited: 107" in summaries[0]["content"]
    verbatim = [m for m in msgs if "<earlier_summary>" not in m["content"]]
    assert len(verbatim) == 2 * 12
    assert verbatim[0]["content"] == "question 8"
    assert conv.folded_turns == 8
    # Only kept turns' ids are citable without a re-read.
    assert 100 not in conv.kept_ids and 119 in conv.kept_ids


async def test_session_state_lists_papers_idea_memories_language(
    migrated_db: aiosqlite.Connection,
) -> None:
    sid = await _session(migrated_db)
    pid, _ = await _paper(migrated_db, "2401.0001", {"Intro": ["a"], "Loss": ["b"]})
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id) VALUES (?, ?)", (sid, pid))
    await migrated_db.execute(
        "INSERT INTO ideas (source_doc, source_section, title, rationale) "
        "VALUES (?, 'Loss', 'Sparsity for segmentation', 'why')", (pid,))
    await migrated_db.execute(
        "INSERT INTO idea_sessions (idea_id, session_id) VALUES (1, ?)", (sid,))
    await migrated_db.execute(
        "INSERT INTO memories (scope, content) VALUES ('global', 'Answer in Japanese')")
    await migrated_db.commit()
    text = await build_session_state(
        migrated_db, sid, {"ui_language": "zh-TW", "slide_context": "SLIDE X"})
    assert "<enabled_papers>" in text
    assert f'id="{pid}"' in text and "Title 2401.0001" in text
    assert 'format="latex"' in text and 'sections="2"' in text
    assert "<idea_context>" in text and "Sparsity for segmentation" in text
    assert "<slide_context>\nSLIDE X\n</slide_context>" in text
    assert "Answer in Japanese" in text
    assert "<ui_language>zh-TW</ui_language>" in text


async def test_session_state_no_papers(migrated_db: aiosqlite.Connection) -> None:
    sid = await _session(migrated_db)
    text = await build_session_state(migrated_db, sid, {})
    assert "<enabled_papers>\n(none)\n</enabled_papers>" in text
    assert "<idea_context>" not in text


async def test_context_blocks_token_counts() -> None:
    blocks = ContextBlocks(
        instructions="You are an agent.",
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        session_state="<enabled_papers>(none)</enabled_papers>",
        conversation=[{"role": "user", "content": "hi"}],
        this_turn=[{"role": "user", "content": "what now?"}],
    )
    counts = blocks.token_counts()
    assert set(counts) == {"instructions", "tools", "session_state",
                           "conversation", "this_turn"}
    assert all(isinstance(v, int) and v > 0 for v in counts.values())


async def test_memories_show_ids_scope_and_escaped_text(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Review #1/#12: the agent needs each memory's id to call forget, and
    the user-sourced text is XML-escaped inside <memories>. Other sessions'
    session memories and inactive ones are left out."""
    sid = await _session(migrated_db)
    other = await _session(migrated_db)
    await migrated_db.execute(
        "INSERT INTO memories (id, scope, content) VALUES (12, 'global', 'likes <b> & tables')")
    await migrated_db.execute(
        "INSERT INTO memories (id, scope, session_id, content) VALUES (13, 'session', ?, 'NTU student')",
        (sid,))
    await migrated_db.execute(
        "INSERT INTO memories (id, scope, session_id, content) VALUES (14, 'session', ?, 'other chat')",
        (other,))
    await migrated_db.execute(
        "INSERT INTO memories (id, scope, content, status) VALUES (15, 'global', 'old', 'superseded')")
    await migrated_db.commit()
    text = await build_session_state(migrated_db, sid, {})
    assert '<memory id="12" scope="global">likes &lt;b&gt; &amp; tables</memory>' in text
    assert '<memory id="13" scope="session">NTU student</memory>' in text
    assert 'id="14"' not in text and 'id="15"' not in text


def test_turn_token_counter_counts_each_block_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review #13: the fixed blocks are tokenised once per turn and each of
    this turn's (append-only) messages once, not the whole context per step."""
    from paperhub.agents.core import context as ctx_mod

    seen: list[str] = []
    real = ctx_mod.count_tokens

    def spy(text: str) -> int:
        seen.append(text)
        return real(text)

    monkeypatch.setattr(ctx_mod, "count_tokens", spy)
    this_turn: list[dict[str, Any]] = [{"role": "user", "content": "first question"}]
    counter = ctx_mod.TurnTokenCounter(ContextBlocks(
        instructions="You are an agent.", tools=[{"type": "function"}],
        session_state="<s/>", conversation=[{"role": "user", "content": "earlier"}],
        this_turn=this_turn))
    first = counter.counts(this_turn)
    this_turn.append({"role": "tool", "content": "a long tool result"})
    second = counter.counts(this_turn)
    assert first["instructions"] == second["instructions"] > 0
    assert second["this_turn"] > first["this_turn"] > 0
    assert seen.count("You are an agent.") == 1
    assert sum("first question" in s for s in seen) == 1
