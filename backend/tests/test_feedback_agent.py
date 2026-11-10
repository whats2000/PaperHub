"""H4 Task 13: the feedback flywheel understands agent runs (``mode='agent'``).

A recorded core-agent turn is rebuilt into a training trajectory from its
``agent:step`` / ``tool:*`` rows alone: library chunks are re-read by id,
ephemeral payloads (SQL rows, search hits) come from the records, and the
system / session-state messages are rebuilt from the ``core_agent`` template
plus the recorded input state.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

import paperhub.agents.core.action_tools as action_tools
from paperhub.agents.core.action_tools import build_default_registry
from paperhub.agents.core.agent import AgentDeps, Final, run_core_agent
from paperhub.agents.core.tools import ToolRegistry
from paperhub.feedback.export import build_export, export_agent_trajectory
from paperhub.feedback.models import ExportRequest, agent_stage
from paperhub.feedback.review import load_review_inputs
from paperhub.feedback.signals import guess_reasons
from paperhub.feedback.trajectory import (
    MEMORIES_WITHHELD,
    UNAVAILABLE,
    FingerprintMismatch,
    TrajectoryError,
)
from paperhub.llm.adapter import ToolCall, ToolTurn
from paperhub.tracing.tracer import Tracer

from .agents_core.test_read_tools import _paper

pytestmark = pytest.mark.asyncio

RUN = 1
SESSION = 1


class FakeAdapter:
    def __init__(self, turns: list[ToolTurn]) -> None:
        self._turns = turns
        self.calls: list[dict[str, Any]] = []

    async def tool_turn(self, **kw: Any) -> ToolTurn:
        # Deep-copy the messages: the loop keeps appending to its list.
        self.calls.append(json.loads(json.dumps(kw["messages"])))
        turn = self._turns.pop(0)
        on_token: Callable[[str], Awaitable[None]] | None = kw.get("on_token")
        if on_token is not None and turn.content:
            await on_token(turn.content)
        return turn


class FakeMcp:
    """The library DB server: every ``sql.query`` returns the same rows."""

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        assert name == "sql.query"
        return {"result": {"columns": ["year", "n"],
                           "rows": [[2023, 4], [2024, 7]]}}


def _calls(*items: tuple[str, dict[str, Any]]) -> ToolTurn:
    return ToolTurn(content="", tool_calls=[
        ToolCall(id=f"gem_{i}__sig==", name=n, arguments=a)
        for i, (n, a) in enumerate(items)
    ])


def _settings(**extra: Any) -> Any:
    return SimpleNamespace(
        agent_model="flagship-test", agent_max_steps=7,
        agent_max_turn_tokens=120000, agent_keep_turns=12, agent_vision=True,
        sql_agent_model="small-test", memory_recall_enabled=False,
        agent_gather_model="small-test", agent_autoapprove_model=None,
        paper_qa_model="pq-test", **extra,
    )


@pytest.fixture
def fake_sql_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """``query_library`` drives the SQL agent; script it: one query, an answer."""

    async def fake(state: Any, *, registry: Any, **_kw: Any) -> AsyncIterator[Any]:
        await registry.call("sql.query", {"sql": "SELECT year, COUNT(*) FROM x"})
        yield "You have 11 papers."

    monkeypatch.setattr(action_tools, "sql_agent_stream", fake)


async def _seed_session(conn: aiosqlite.Connection) -> None:
    await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (?, 'c')",
                       (SESSION,))
    await conn.execute(
        "INSERT INTO runs (id, session_id, status, mode) VALUES (?, ?, 'running', "
        "'agent')", (RUN, SESSION))
    await conn.commit()


async def _enable(conn: aiosqlite.Connection, pid: int) -> None:
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id) VALUES (?, ?)",
        (SESSION, pid))
    await conn.commit()


async def _drive_and_persist(
    conn: aiosqlite.Connection, adapter: FakeAdapter, registry: ToolRegistry,
    question: str, history: list[dict[str, Any]] | None = None,
    state_extra: dict[str, Any] | None = None, settings: Any = None,
) -> Final:
    """Run one agent turn and persist it the way ``api/chat.py`` does: the
    user message, the final answer and the agent record."""
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'user', ?, ?)", (SESSION, question, RUN))
    await conn.commit()
    deps = AgentDeps(adapter=adapter, tracer=Tracer(conn, run_id=RUN, branch=""),
                     conn=conn, settings=settings or _settings(), registry=registry,
                     mcp_registry=FakeMcp())
    state: dict[str, Any] = {"run_id": RUN, "session_id": SESSION,
                             "user_message": question, "history": history or [],
                             **(state_extra or {})}
    events = [e async for e in run_core_agent(state, deps)]  # type: ignore[arg-type]
    final = events[-1]
    assert isinstance(final, Final)
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'assistant', ?, ?)", (SESSION, final.text, RUN))
    await conn.execute(
        "UPDATE runs SET status = 'ok', agent_record_json = ? WHERE id = ?",
        (json.dumps({**final.record, "ui_events": []}), RUN))
    await conn.commit()
    return final


def _normalise_ids(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool-call ids are provider tokens (Gemini's carry a thought signature);
    a trajectory numbers them itself. Compare everything else verbatim."""
    out: list[dict[str, Any]] = []
    n = 0
    names: dict[str, str] = {}
    for m in json.loads(json.dumps(messages)):
        for c in m.get("tool_calls") or []:
            names[c["id"]] = f"id{n}"
            c["id"] = f"id{n}"
            n += 1
        if "tool_call_id" in m:
            m["tool_call_id"] = names[m["tool_call_id"]]
        out.append(m)
    return out


# ------------------------------------------------------------ the trajectory


async def test_trajectory_rebuilds_what_the_model_saw(
    migrated_db: aiosqlite.Connection, fake_sql_agent: None,
) -> None:
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0001", {
        "Loss": ["The loss is L = a + b.", "We tune b with AdamW."],
        "Data": ["We train on ImageNet."],
    })
    await _enable(conn, pid)
    loss, adam = ids["The loss is L = a + b."], ids["We tune b with AdamW."]
    # An earlier turn in the chat (the conversation block of this turn).
    await conn.execute("INSERT INTO runs (id, session_id, status) VALUES (2, ?, 'ok')",
                       (SESSION,))
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES "
        "(?, 'user', 'What loss?', 2), (?, 'assistant', ?, 2)",
        (SESSION, SESSION, f"An additive loss [chunk:{loss}]."))
    await conn.execute(
        "INSERT INTO memories (scope, session_id, content, status) "
        "VALUES ('global', NULL, 'Answer briefly.', 'active')")
    await conn.commit()

    adapter = FakeAdapter([
        _calls(("read_chunks", {"ids": [loss, 999999]}),
               ("search_chunks", {"query": "AdamW"}),
               ("query_library", {"question": "papers per year?"})),
        _calls(("read_section", {"paper": pid, "section": "Loss"}),
               ("list_sections", {"paper": pid}),
               ("read_section", {"paper": pid, "section": "Nope"})),
        ToolTurn(content=f"L = a + b [chunk:{loss}], tuned with AdamW [chunk:{adam}]."),
    ])
    history = [{"role": "user", "content": "What loss?"},
               {"role": "assistant", "content": f"An additive loss [chunk:{loss}]."}]
    await _drive_and_persist(
        conn, adapter, build_default_registry(_settings()), "And the optimiser?",
        history=history, state_extra={"ui_language": "zh-TW"})
    # The memory changes after the turn: the export must use what was recorded.
    await conn.execute("UPDATE memories SET status = 'superseded'")
    await conn.commit()

    traj = await export_agent_trajectory(conn, RUN, include_private=True)
    assert traj["private"] is False

    seen = adapter.calls[-1]  # the final step saw the whole turn
    final = {"role": "assistant",
             "content": f"L = a + b [chunk:{loss}], tuned with AdamW [chunk:{adam}]."}
    assert _normalise_ids(traj["messages"]) == _normalise_ids([*seen, final])
    assert traj["tools"] == build_default_registry(_settings()).schemas()
    # Spot checks on what the equality covers.
    assert seen[0]["role"] == "system" and "7 tool steps" in seen[0]["content"]
    assert "Answer briefly." in traj["messages"][1]["content"]
    # Memories' provenance is not tracked (a global memory may come from any
    # chat): without include_private the block is withheld.
    closed = await export_agent_trajectory(conn, RUN)
    assert "Answer briefly." not in json.dumps(closed, ensure_ascii=False)
    assert MEMORIES_WITHHELD in closed["messages"][1]["content"]
    assert closed["messages"][2:] == traj["messages"][2:]
    sql_result = next(m["content"] for m in traj["messages"]
                      if m.get("role") == "tool" and "11 papers" in m["content"])
    assert "2024 | 7" in sql_result


async def test_trajectory_rebuilds_gather_search_and_pending_confirm(
    migrated_db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """gather (re-read by id), search_papers (candidates from the record), a
    confirm-tier call left pending and its retry in a later step."""
    import paperhub.api.chat as chat_module
    from paperhub.agents.research import (
        FinalOnlyMessage,
        SearchCandidate,
        SearchResultsYield,
    )

    async def fake_search(state: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield SearchResultsYield(candidates=[SearchCandidate(
            paper_id="arxiv:2501.00001", title="Sparse Mixtures", authors=["A"],
            year=2025, abstract=None, arxiv_id="2501.00001", has_open_pdf=True,
            reason="on topic", finalize=False)])
        yield FinalOnlyMessage("")

    monkeypatch.setattr(chat_module, "paper_search", fake_search)
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0006", {
        "Method": ["Experts are routed top-2.", "A balancing loss is added."]})
    await _enable(conn, pid)
    top2 = ids["Experts are routed top-2."]
    adapter = FakeAdapter([
        _calls(("gather", {"papers": [pid], "brief": "How are experts routed?"}),
               ("search_papers", {"query": "sparse mixtures", "mode": "suggest"}),
               ("remove_paper", {"paper": pid})),
        # gather's own reader: read, then select
        _calls(("read_section", {"paper": pid, "section": "Method"})),
        _calls(("select", {"chunks": [{"id": top2, "reason": "routing rule"}]})),
        _calls(("remove_paper", {"paper": pid})),
        ToolTurn(content=f"Top-2 routing [chunk:{top2}]."),
    ])
    await _drive_and_persist(
        conn, adapter, build_default_registry(), "Routing?",
        settings=_settings(papers_cache_dir=tmp_path))

    traj = await export_agent_trajectory(conn, RUN)
    seen = adapter.calls[-1]
    final = {"role": "assistant", "content": f"Top-2 routing [chunk:{top2}]."}
    assert _normalise_ids(traj["messages"]) == _normalise_ids([*seen, final])
    tool_texts = [m["content"] for m in traj["messages"] if m.get("role") == "tool"]
    assert "Experts are routed top-2." in tool_texts[0]
    assert "arxiv:2501.00001 | Sparse Mixtures" in tool_texts[1]
    assert tool_texts[2].startswith("Waiting for the user to confirm")
    assert tool_texts[3].startswith("Still waiting for the user to confirm")


async def test_private_chunk_read_makes_the_run_unexportable(
    migrated_db: aiosqlite.Connection, fake_sql_agent: None,
) -> None:
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0002", {"Secret": ["Our private recipe."]})
    await conn.execute("UPDATE paper_content SET is_private = 1 WHERE id = ?", (pid,))
    await _enable(conn, pid)
    chunk = ids["Our private recipe."]
    adapter = FakeAdapter([
        _calls(("read_chunks", {"ids": [chunk]}),
               ("search_chunks", {"query": "recipe"})),
        ToolTurn(content=f"The recipe [chunk:{chunk}]."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(_settings()),
                             "What recipe?")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'up')",
        (RUN,))
    await conn.commit()

    with pytest.raises(TrajectoryError, match="private"):
        await export_agent_trajectory(conn, RUN)
    shown = await export_agent_trajectory(conn, RUN, include_private=True)
    assert shown["private"] is True
    assert "Our private recipe." in json.dumps(shown, ensure_ascii=False)

    # The SFT/DPO builders apply the same fail-closed rule.
    assert (await build_export(conn, ExportRequest(include_private=False))).sft == []
    plan = await build_export(conn, ExportRequest(include_private=True))
    assert plan.includes_private and len(plan.sft) == 1
    assert "Our private recipe." in json.dumps(plan.sft[0], ensure_ascii=False)


async def test_agent_run_sft_line_is_the_trajectory(
    migrated_db: aiosqlite.Connection, fake_sql_agent: None,
) -> None:
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0003", {"Intro": ["Attention is all."]})
    await _enable(conn, pid)
    chunk = ids["Attention is all."]
    adapter = FakeAdapter([
        _calls(("read_chunks", {"ids": [chunk]})),
        ToolTurn(content=f"Attention [chunk:{chunk}]."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(_settings()),
                             "What is used?")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, correction) "
        "VALUES (?, 'explicit', 'up', NULL)", (RUN,))
    await conn.commit()
    plan = await build_export(conn, ExportRequest())
    traj = await export_agent_trajectory(conn, RUN)
    assert traj["private"] is False
    assert plan.sft == [{"messages": traj["messages"], "tools": traj["tools"]}]
    assert plan.sft[0]["messages"][-1] == {"role": "assistant",
                                          "content": f"Attention [chunk:{chunk}]."}

    # A correction makes a DPO pair whose prompt is the trajectory before the
    # final answer.
    await conn.execute(
        "UPDATE feedback SET polarity = 'down', correction = 'Self-attention.'")
    await conn.commit()
    plan = await build_export(conn, ExportRequest())
    assert plan.dpo == [{
        "prompt": traj["messages"][:-1],
        "chosen": [{"role": "assistant", "content": "Self-attention."}],
        "rejected": [traj["messages"][-1]],
        "tools": traj["tools"],
    }]


async def test_private_session_leaks_nothing_without_include_private(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Private titles reach a turn through error text (the enabled-paper
    listing), the confirm summary and the session state, and its idea and
    slide context may quote a private paper: such a run is not exportable
    unless include_private, and nothing of it is returned."""
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0010", {"Method": ["Public text."]})
    secret, _ = await _paper(conn, "2401.0011", {"S": ["Secret text."]})
    await conn.execute(
        "UPDATE paper_content SET is_private = 1, title = 'Secret Title' "
        "WHERE id = ?", (secret,))
    await _enable(conn, pid)
    await _enable(conn, secret)
    adapter = FakeAdapter([
        _calls(("read_section", {"paper": 999999, "section": "x"}),
               ("remove_paper", {"paper": secret})),
        ToolTurn(content="Done."),
    ])
    await _drive_and_persist(
        conn, adapter, build_default_registry(), "Go",
        state_extra={"idea_context": "<idea>IDEA-TEXT</idea>",
                     "slide_context": "SLIDE-TEXT"})
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'up')",
        (RUN,))
    await conn.commit()

    with pytest.raises(TrajectoryError, match="private"):
        await export_agent_trajectory(conn, RUN)
    plan = await build_export(conn, ExportRequest(include_private=False))
    dumped = json.dumps([plan.sft, plan.dpo], ensure_ascii=False)
    for leak in ("Secret Title", "IDEA-TEXT", "SLIDE-TEXT"):
        assert leak not in dumped
    assert plan.sft == []
    shown = await export_agent_trajectory(conn, RUN, include_private=True)
    assert shown["private"] is True
    assert "Secret Title" in json.dumps(shown, ensure_ascii=False)


async def test_idea_context_with_a_private_library_is_unexportable(
    migrated_db: aiosqlite.Connection,
) -> None:
    """An idea chat's context is built from library-wide mechanism cards, so
    it may quote any paper: fail closed when the library holds a private one."""
    conn = migrated_db
    await _seed_session(conn)
    pid, _ = await _paper(conn, "2401.0012", {"Method": ["Public."]})
    secret, _ = await _paper(conn, "2401.0013", {"S": ["Hidden."]})
    await conn.execute("UPDATE paper_content SET is_private = 1 WHERE id = ?",
                       (secret,))
    await _enable(conn, pid)
    adapter = FakeAdapter([ToolTurn(content="Idea answer.")])
    await _drive_and_persist(conn, adapter, build_default_registry(), "Go",
                             state_extra={"idea_context": "<idea>X</idea>"})
    with pytest.raises(TrajectoryError, match="private"):
        await export_agent_trajectory(conn, RUN)


async def test_memories_are_withheld_from_build_export_unless_private_allowed(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    from paperhub.feedback.export import write_export

    conn = migrated_db
    await _seed_session(conn)
    await conn.execute(
        "INSERT INTO memories (scope, session_id, content, status) "
        "VALUES ('global', NULL, 'My employer is Acme.', 'active')")
    await conn.commit()
    adapter = FakeAdapter([ToolTurn(content="Hi.")])
    await _drive_and_persist(conn, adapter, build_default_registry(), "Hello")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'up')",
        (RUN,))
    await conn.commit()

    closed = await build_export(conn, ExportRequest(include_private=False))
    assert "Acme" not in json.dumps(closed.sft, ensure_ascii=False)
    assert MEMORIES_WITHHELD in closed.sft[0]["messages"][1]["content"]
    card = (write_export(closed, tmp_path) / "README.md").read_text(encoding="utf-8")
    assert "memories" in card.lower() and "withheld" in card.lower()
    opened = await build_export(conn, ExportRequest(include_private=True))
    assert "Acme" in json.dumps(opened.sft, ensure_ascii=False)


async def test_fingerprint_is_recorded_and_a_changed_prompt_or_tools_drops(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    from paperhub.feedback.export import write_export

    conn = migrated_db
    await _seed_session(conn)
    adapter = FakeAdapter([ToolTurn(content="Hi.")])
    await _drive_and_persist(conn, adapter, build_default_registry(), "Hello")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'up')",
        (RUN,))
    await conn.commit()
    async with conn.execute(
        "SELECT result_summary_json FROM tool_calls WHERE run_id = ? "
        "AND tool = 'agent:step' ORDER BY step_index LIMIT 1", (RUN,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    record = json.loads(row[0])
    state = record["session_state"]
    assert state["prompt_slot"] == "core_agent/v8"
    assert len(state["tools_sha256"]) == 64 and len(state["system_sha256"]) == 64
    assert (await export_agent_trajectory(conn, RUN))["messages"]

    async def set_state(new_state: dict[str, Any]) -> None:
        await conn.execute(
            "UPDATE tool_calls SET result_summary_json = ? WHERE run_id = ? "
            "AND tool = 'agent:step'",
            (json.dumps({**record, "session_state": new_state}), RUN))
        await conn.commit()

    for key in ("tools_sha256", "system_sha256", "prompt_slot"):
        await set_state({**state, key: "changed"})
        with pytest.raises(FingerprintMismatch):
            await export_agent_trajectory(conn, RUN)
    plan = await build_export(conn, ExportRequest())
    assert plan.sft == [] and plan.agent_fingerprint_drops == 1
    card = (write_export(plan, tmp_path) / "README.md").read_text(encoding="utf-8")
    assert "1 agent turn(s) left out" in card

    # Ruling R18: a run recorded before the fingerprint exports as is.
    await set_state({k: v for k, v in state.items()
                     if k not in ("prompt_slot", "tools_sha256", "system_sha256")})
    assert len((await build_export(conn, ExportRequest())).sft) == 1


async def test_layout_rebuild_checks_the_recorded_chunk(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A re-ingest can move a figure to another chunk or change the list:
    the rebuilt result is then unavailable, never the new text."""
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0014", {
        "Results": ["Table 1: accuracy 91%.", "Table 1 v2: accuracy 88%."]})
    old, new = ids["Table 1: accuracy 91%."], ids["Table 1 v2: accuracy 88%."]

    async def set_layout(chunk: int, caption: str) -> None:
        await conn.execute(
            "UPDATE paper_content SET layout_json = ? WHERE id = ?",
            (json.dumps([{"label": "Table 1", "kind": "table", "page": 2,
                          "chunk_id": chunk, "caption": caption}]), pid))
        await conn.commit()

    await set_layout(old, "Accuracy")
    await _enable(conn, pid)
    adapter = FakeAdapter([
        _calls(("list_figures_tables", {"paper": pid}),
               ("read_layout_object", {"paper": pid, "label": "Table 1"})),
        ToolTurn(content=f"91% [chunk:{old}]."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(), "Acc?")
    seen = adapter.calls[-1]
    same = await export_agent_trajectory(conn, RUN)
    assert _normalise_ids(same["messages"][:-1]) == _normalise_ids(seen)

    await set_layout(new, "Accuracy (revised)")
    moved = await export_agent_trajectory(conn, RUN, include_private=True)
    tools = [m["content"] for m in moved["messages"] if m.get("role") == "tool"]
    assert tools == [UNAVAILABLE, UNAVAILABLE]
    assert moved["private"] is True
    with pytest.raises(TrajectoryError):
        await export_agent_trajectory(conn, RUN)


async def test_slide_tool_result_rebuilds_from_its_record(
    migrated_db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import paperhub.api.chat as chat_module
    from paperhub.agents.research import FinalOnlyMessage

    async def fake_report(state: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield chat_module.DeckYield(deck={
            "deck_id": 1, "session_id": SESSION, "page_count": 9,
            "title": "My Deck", "status": "ok", "contributing_papers": [],
            "has_notes": True, "version_id": 3})
        yield FinalOnlyMessage("Deck ready.")

    monkeypatch.setattr(chat_module, "report_stream", fake_report)
    conn = migrated_db
    await _seed_session(conn)
    adapter = FakeAdapter([
        _calls(("create_deck", {"brief": "talk"})),
        ToolTurn(content="Your deck is ready."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(), "Slides",
                             settings=_settings(papers_cache_dir=tmp_path))
    traj = await export_agent_trajectory(conn, RUN)
    assert _normalise_ids(traj["messages"][:-1]) == _normalise_ids(adapter.calls[-1])
    tool = next(m["content"] for m in traj["messages"] if m.get("role") == "tool")
    assert tool == ("Deck 'My Deck': 9 pages, status ok, version 3, with speaker "
                    "notes.\nDeck ready.")


async def test_unmatched_tool_row_is_not_paired_by_name(
    migrated_db: aiosqlite.Connection,
) -> None:
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0015", {"M": ["Text one."]})
    await _enable(conn, pid)
    chunk = ids["Text one."]
    adapter = FakeAdapter([_calls(("read_chunks", {"ids": [chunk]})),
                           ToolTurn(content="Ok.")])
    await _drive_and_persist(conn, adapter, build_default_registry(), "Q")
    await conn.execute(
        "UPDATE tool_calls SET args_redacted_json = ? WHERE run_id = ? "
        "AND tool = 'tool:read_chunks'", (json.dumps({"ids": [1, 2]}), RUN))
    await conn.commit()
    with pytest.raises(TrajectoryError, match="no trace row"):
        await export_agent_trajectory(conn, RUN)


async def test_dataset_card_notes_agent_conventions(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    from paperhub.feedback.export import write_export

    conn = migrated_db
    await _seed_session(conn)
    adapter = FakeAdapter([ToolTurn(content="Hi.")])
    await _drive_and_persist(conn, adapter, build_default_registry(), "Hello")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'up')",
        (RUN,))
    await conn.commit()
    plan = await build_export(conn, ExportRequest())
    card = (write_export(plan, tmp_path) / "README.md").read_text(encoding="utf-8")
    assert "after citation validation" in card
    assert "`function.arguments` is a JSON string" in card
    assert "`content` is null" in card
    assert "original run's session state" in card


# ------------------------------------------------------- pre-H4 runs unchanged


async def test_pipeline_run_exports_exactly_as_before(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A pre-H4 (router + pipeline) run keeps today's sample shape, byte for byte."""
    conn = migrated_db
    await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'c')")
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) VALUES (5, 'k', 'arxiv', "
        "'2401.1', 'A \"Paper\"', '', '', '')")
    await conn.execute(
        "INSERT INTO chunks (id, paper_content_id, char_start, char_end, text) "
        "VALUES (40, 5, 0, 1, 'Transformers use attention.')")
    for rid, q, a in [(1, "hi", "hello"),
                      (2, "Uses?", "Attention [chunk:40]."),
                      (3, "Year?", "2019")]:
        await conn.execute(
            "INSERT INTO runs (id, session_id, status, routing_decision_json) "
            "VALUES (?, 1, 'ok', '{\"intent\": \"paper_qa\"}')", (rid,))
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) VALUES "
            "(1, 'user', ?, ?), (1, 'assistant', ?, ?)", (q, rid, a, rid))
        await conn.execute(
            "INSERT INTO tool_calls (run_id, step_index, agent, tool, model, "
            "latency_ms, status) VALUES (?, 0, 'research', 'paper_qa:finalize', "
            "'gemini/flagship', 1, 'ok')", (rid,))
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, created_at) VALUES "
        "(2, 'explicit', 'up', '2026-11-03 10:00:00')")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, correction, created_at) "
        "VALUES (3, 'explicit', 'down', '2017', '2026-11-03 11:00:00')")
    await conn.commit()

    plan = await build_export(conn, ExportRequest())
    sources = {"role": "system", "content":
               '<sources>\n<source chunk="40" paper="A &quot;Paper&quot;">\n'
               "Transformers use attention.\n</source>\n</sources>"}
    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello"}]
    assert json.dumps(plan.sft, ensure_ascii=False) == json.dumps([{"messages": [
        sources, *history, {"role": "user", "content": "Uses?"},
        {"role": "assistant", "content": "Attention [chunk:40]."},
    ]}], ensure_ascii=False)
    assert json.dumps(plan.dpo, ensure_ascii=False) == json.dumps([{
        "prompt": [*history, {"role": "user", "content": "Uses?"},
                   {"role": "assistant", "content": "Attention [chunk:40]."},
                   {"role": "user", "content": "Year?"}],
        "chosen": [{"role": "assistant", "content": "2017"}],
        "rejected": [{"role": "assistant", "content": "2019"}],
    }], ensure_ascii=False)
    with pytest.raises(ValueError, match="not an agent run"):
        await export_agent_trajectory(conn, 2)


# ------------------------------------------------- attribution and review


async def test_stage_of_agent_steps() -> None:
    assert agent_stage("agent:step", {"answer": "x"}) == "answer"
    assert agent_stage("agent:step", {"tool_calls": [{"name": "gather"}]}) is None
    for tool in ("read_section", "read_chunks", "read_layout_object",
                 "search_chunks", "gather", "query_library", "list_sections"):
        assert agent_stage(f"tool:{tool}", {}) == "retrieval", tool
    assert agent_stage("tool:search_papers", {}) == "router"
    assert agent_stage("agent:step", {"answer": "x", "citations_removed": [9]}) \
        == "citation"


async def test_dropped_citation_guesses_citation_and_reviewer_sees_it(
    migrated_db: aiosqlite.Connection, fake_sql_agent: None,
) -> None:
    conn = migrated_db
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0004", {"Method": ["We use dropout."]})
    await _enable(conn, pid)
    chunk = ids["We use dropout."]
    adapter = FakeAdapter([
        _calls(("read_section", {"paper": pid, "section": "Method"}),
               ("query_library", {"question": "how many?"})),
        ToolTurn(content=f"Dropout [chunk:{chunk}] and AdamW [chunk:999999]."),
    ])
    final = await _drive_and_persist(conn, adapter,
                                     build_default_registry(_settings()), "How?")
    assert final.record["citations_removed"] == [999999]

    assert "citation" in await guess_reasons(conn, RUN)

    inputs = await load_review_inputs(conn, RUN)
    assert inputs is not None
    lines = inputs.trace_summary.splitlines()
    assert lines[0] == 'route: agent (refs: [P1] "Title 2401.0004")'
    assert "read: P1 §Method" in lines
    assert "sql: SELECT year, COUNT(*) FROM x" in lines
    assert f'cited: chunk {chunk} (P1 §Method) "We use dropout."' in lines
    assert "citation removed: chunk 999999 (never read)" in lines
    assert inputs.cited_chunk_ids == [chunk]


async def test_agent_run_that_read_nothing_guesses_wrong_source(
    migrated_db: aiosqlite.Connection, fake_sql_agent: None,
) -> None:
    conn = migrated_db
    await _seed_session(conn)
    pid, _ids = await _paper(conn, "2401.0005", {"Method": ["Text."]})
    await _enable(conn, pid)
    adapter = FakeAdapter([
        _calls(("search_chunks", {"query": "zebra"}),
               ("read_section", {"paper": pid, "section": "Missing"})),
        ToolTurn(content="I could not find it."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(_settings()),
                             "Zebras?")
    reasons = await guess_reasons(conn, RUN)
    assert reasons == ["wrong_source"]


async def test_trajectory_of_a_turn_answered_by_query_library(
    migrated_db: aiosqlite.Connection, fake_sql_agent: None, tmp_path: Path,
) -> None:
    """query_library with answer_user ends the turn with the SQL agent's
    answer (no second model step). The trajectory is marked and ends at the
    tool call; it is an SFT sample, never a DPO pair."""
    from paperhub.feedback.export import write_export

    conn = migrated_db
    await _seed_session(conn)
    adapter = FakeAdapter([
        _calls(("query_library", {"question": "How many papers?",
                                  "answer_user": True})),
    ])
    final = await _drive_and_persist(
        conn, adapter, build_default_registry(_settings()), "How many papers?")
    assert final.text == "You have 11 papers."
    assert len(adapter.calls) == 1
    traj = await export_agent_trajectory(conn, RUN)
    assert traj["answer_from_tool"] == "query_library"
    msgs = traj["messages"]
    assert msgs[-1]["role"] == "assistant"
    assert [c["function"]["name"] for c in msgs[-1]["tool_calls"]] == ["query_library"]
    assert not any(m["role"] == "tool" for m in msgs)
    assert "You have 11 papers." not in json.dumps(msgs)
    assert _normalise_ids(msgs[:-1]) == _normalise_ids(adapter.calls[0])

    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity) VALUES (?, 'explicit', 'up')",
        (RUN,))
    await conn.commit()
    plan = await build_export(conn, ExportRequest())
    assert plan.sft == [{"messages": msgs, "tools": traj["tools"],
                         "answer_from_tool": "query_library"}]
    card = (write_export(plan, tmp_path / "a") / "README.md").read_text(encoding="utf-8")
    assert '1 agent sample(s) carry `"answer_from_tool"`' in card

    # A correction of that answer is about the SQL agent's text: no pair.
    await conn.execute(
        "UPDATE feedback SET polarity = 'down', correction = 'You have 12.'")
    await conn.commit()
    plan = await build_export(conn, ExportRequest())
    assert plan.dpo == [] and plan.agent_tool_answer_pairs == 1
    card = (write_export(plan, tmp_path / "b") / "README.md").read_text(encoding="utf-8")
    assert "1 DPO pair(s) left out" in card


# ------------------------------------------- review #7: the live history


async def _errored_turn_then_agent_turn(conn: aiosqlite.Connection) -> tuple[FakeAdapter, int]:
    await _seed_session(conn)
    pid, ids = await _paper(conn, "2401.0701", {"Intro": ["Attention is all."]})
    await _enable(conn, pid)
    chunk = ids["Attention is all."]
    # An earlier turn that errored: its error text is an assistant message,
    # which the client leaves out of the history it sends.
    await conn.execute("INSERT INTO runs (id, session_id, status) VALUES (2, ?, 'error')",
                       (SESSION,))
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES "
        "(?, 'user', 'First question?', 2), (?, 'assistant', 'Error: boom', 2)",
        (SESSION, SESSION))
    await conn.commit()
    adapter = FakeAdapter([
        _calls(("read_chunks", {"ids": [chunk]})),
        ToolTurn(content=f"Attention [chunk:{chunk}]."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(_settings()),
                             "What is used?",
                             history=[{"role": "user", "content": "First question?"}])
    return adapter, chunk


async def test_trajectory_leaves_out_error_answers_like_the_live_turn(
    migrated_db: aiosqlite.Connection,
) -> None:
    adapter, chunk = await _errored_turn_then_agent_turn(migrated_db)
    traj = await export_agent_trajectory(migrated_db, RUN)
    assert "Error: boom" not in json.dumps(traj["messages"])
    final = {"role": "assistant", "content": f"Attention [chunk:{chunk}]."}
    assert _normalise_ids(traj["messages"]) == _normalise_ids(
        [*adapter.calls[-1], final])


async def test_conversation_mismatch_skips_the_sample_and_is_counted(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.feedback.trajectory import ConversationMismatch

    conn = migrated_db
    await _errored_turn_then_agent_turn(conn)
    # The history the turn saw no longer rebuilds (a message vanished).
    await conn.execute("DELETE FROM messages WHERE run_id = 2")
    await conn.execute(
        "INSERT INTO feedback (run_id, kind, polarity, correction) "
        "VALUES (?, 'explicit', 'up', NULL)", (RUN,))
    await conn.commit()
    with pytest.raises(ConversationMismatch):
        await export_agent_trajectory(conn, RUN)
    plan = await build_export(conn, ExportRequest())
    assert plan.sft == [] and plan.agent_conversation_drops == 1


async def test_trajectory_rebuilds_a_figure_view_with_its_image(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    """``view_figure``: the tool message and the image message that follows
    it are rebuilt from the asset; a changed image makes the run private."""
    from PIL import Image

    conn = migrated_db
    await _seed_session(conn)
    pid, _ids = await _paper(conn, "2401.0002", {"Results": ["AP rises."]})
    asset = tmp_path / "asset"
    (asset / "figures").mkdir(parents=True)
    Image.new("RGB", (40, 20), (0, 90, 0)).save(asset / "figures" / "fig-003.png")
    (asset / "figures.json").write_text(json.dumps([{
        "id": "fig-003", "caption": "Fig. 3 | Bar charts of AP", "page": 4,
        "section": None, "image_path": "figures/fig-003.png"}]), encoding="utf-8")
    await conn.execute("UPDATE paper_content SET source_dir_path = ? WHERE id = ?",
                       (str(tmp_path), pid))
    await conn.commit()
    await _enable(conn, pid)

    adapter = FakeAdapter([
        _calls(("list_figures_tables", {"paper": pid})),
        _calls(("view_figure", {"paper": pid, "figure": "Figure 3"})),
        ToolTurn(content="Four bar charts; the highest is about 77%."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(_settings()),
                             "How many bar charts in Fig. 3?")

    traj = await export_agent_trajectory(conn, RUN, include_private=True)
    assert traj["private"] is False
    seen = adapter.calls[-1]
    final = {"role": "assistant", "content": "Four bar charts; the highest is about 77%."}
    assert _normalise_ids(traj["messages"]) == _normalise_ids([*seen, final])
    assert any(isinstance(m["content"], list) for m in traj["messages"])

    # The image file changes after the turn: not what the model saw.
    Image.new("RGB", (40, 20), (200, 0, 0)).save(asset / "figures" / "fig-003.png")
    changed = await export_agent_trajectory(conn, RUN, include_private=True)
    assert changed["private"] is True
    assert UNAVAILABLE in json.dumps(changed["messages"])


async def test_citing_a_private_papers_figure_makes_the_run_private(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A ``[fig:P/ID]`` in an answer shows paper P's content, like a chunk."""
    from paperhub.feedback.trajectory import _Library

    conn = migrated_db
    pid, _ids = await _paper(conn, "2401.0003", {"Results": ["r"]})
    await conn.execute("UPDATE paper_content SET is_private = 1 WHERE id = ?", (pid,))
    await conn.commit()
    lib = _Library(conn, include_private=False)
    await lib.mark_cited([f"About 77% [fig:{pid}/fig-003]."])
    assert lib.private is True


async def test_trajectory_rebuilds_a_zoomed_region(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    """A zoom step is rebuilt by running the tool again: the same tool
    message and page image; a changed page PDF makes the run private."""
    import pymupdf

    conn = migrated_db
    await _seed_session(conn)
    pid, _ids = await _paper(conn, "2401.0004", {"Results": ["AP rises."]})
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Table 2: AP 0.732", fontsize=12)
    doc.save(tmp_path / "source.pdf")
    await conn.execute("UPDATE paper_content SET source_dir_path = ? WHERE id = ?",
                       (str(tmp_path), pid))
    await conn.commit()
    await _enable(conn, pid)

    token = f"[view:{pid}/1/0,0,500,200]"
    adapter = FakeAdapter([
        _calls(("zoom", {"paper": pid, "page": 1, "box": [0, 0, 500, 200]})),
        ToolTurn(content=f"AP is 0.732 {token}."),
    ])
    await _drive_and_persist(conn, adapter, build_default_registry(_settings()),
                             "What AP does Table 2 report?")
    traj = await export_agent_trajectory(conn, RUN, include_private=True)
    assert traj["private"] is False
    final = {"role": "assistant", "content": f"AP is 0.732 {token}."}
    assert _normalise_ids(traj["messages"]) == _normalise_ids([*adapter.calls[-1], final])

    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "Changed", fontsize=12)
    doc.save(tmp_path / "source.pdf")
    changed = await export_agent_trajectory(conn, RUN, include_private=True)
    assert changed["private"] is True
