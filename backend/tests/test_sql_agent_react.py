"""Task 3 — bounded ReAct loop for the SQL agent (library_stats).

These tests exercise the LOOP behaviour of ``sql_agent_stream`` after the
rewrite from the fixed plan->query->repair->answer pipeline to an agentic
ReAct loop. Each model round returns a ``SqlRoundAction``; ``action="query"``
runs a validated ``sql.query`` and feeds rows back, ``action="finalize"`` ends
the loop. Task 4 will turn ``final_action.papers`` into curated cards — here we
only assert the loop control flow, tracing, and that the answer text streams.
"""
from __future__ import annotations

from typing import Any

import aiosqlite
import pytest

from paperhub.agents.research import SearchResultsYield, ToolStepYield
from paperhub.agents.sql_agent import sql_agent_stream
from paperhub.agents.state import AgentState
from paperhub.models.sql_domain import SqlPaperPick, SqlRoundAction
from paperhub.tracing.tracer import Tracer

# ---------------------------------------------------------------------------
# Stub adapter: returns a queued list of SqlRoundAction from .structured(),
# records the variables it was called with so tests can assert the prompt
# contract (all 8 vars present). .stream() is not used by the loop.
# ---------------------------------------------------------------------------


class _ScriptedAdapter:
    """Returns the next queued ``SqlRoundAction`` per ``structured`` call.

    If the queue is exhausted it keeps returning the last action (so a test
    that wants "always query" only needs to queue one query action).
    """

    def __init__(self, actions: list[SqlRoundAction]) -> None:
        self._actions = actions
        self.calls: list[dict[str, Any]] = []

    async def structured(  # type: ignore[no-untyped-def]
        self, *, slot: str, variables: dict[str, Any], response_model, model: str, **kw: Any
    ) -> Any:
        self.calls.append(variables)
        assert response_model is SqlRoundAction
        idx = min(len(self.calls) - 1, len(self._actions) - 1)
        return self._actions[idx]

    def stream(self, **kw: Any):  # pragma: no cover - loop doesn't stream the model
        raise AssertionError("ReAct loop must not call adapter.stream()")


# ---------------------------------------------------------------------------
# Stub registry: schema describes + a scriptable sql.query result.
# ---------------------------------------------------------------------------


def _schema_columns(table: str) -> dict[str, Any]:
    if table == "paper_content":
        return {"columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "title", "type": "TEXT"},
            {"name": "year", "type": "INTEGER"},
            {"name": "abstract", "type": "TEXT"},
        ]}
    return {"columns": [
        {"name": "id", "type": "INTEGER"},
        {"name": "session_id", "type": "INTEGER"},
        {"name": "paper_content_id", "type": "INTEGER"},
    ]}


class _QueryRegistry:
    """``sql.query`` returns ``query_results[n]`` for the n-th query call (or
    the last entry if exhausted). Counts describe + query calls for asserts."""

    def __init__(self, query_results: list[Any]) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._results = query_results
        self._query_n = 0

    async def call(self, name: str, args: dict) -> Any:
        self.calls.append((name, args))
        if name == "sql.describe":
            return _schema_columns(args.get("table", ""))
        if name == "sql.query":
            res = self._results[min(self._query_n, len(self._results) - 1)]
            self._query_n += 1
            return res
        raise AssertionError(name)

    @property
    def query_calls(self) -> int:
        return sum(1 for c in self.calls if c[0] == "sql.query")

    @property
    def describe_calls(self) -> int:
        return sum(1 for c in self.calls if c[0] == "sql.describe")


def _state() -> AgentState:
    return {
        "run_id": 1, "session_id": 1,
        "user_message": "how many papers do I have?",
        "effective_query": "how many papers do I have?",
        "response_language": "English",
    }


async def _seed_run(conn: aiosqlite.Connection) -> Tracer:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
    await conn.commit()
    return Tracer(conn, run_id=1, branch="")


async def _drain(stream) -> list[Any]:
    items: list[Any] = []
    async for item in stream:
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# 1. One-shot finalize: round 1 finalizes → no sql.query runs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_shot_finalize_runs_no_query(migrated_db: aiosqlite.Connection) -> None:
    tracer = await _seed_run(migrated_db)
    adapter = _ScriptedAdapter([
        SqlRoundAction(action="finalize", sql=None, answer="You have 3 papers.", papers=[]),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[3]]}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
    ))
    out = "".join(x for x in items if isinstance(x, str))
    assert "You have 3 papers." in out
    # No sql.query beyond the two schema describes.
    assert reg.query_calls == 0
    assert reg.describe_calls == 2
    # The model round was traced as sql:react.
    async with migrated_db.execute(
        "SELECT tool FROM tool_calls WHERE run_id = 1 AND tool = 'sql:react'"
    ) as cur:
        assert len(await cur.fetchall()) == 1


# ---------------------------------------------------------------------------
# 2. Refine: round 1 query → round 2 finalize → exactly one sql.query.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refine_one_query_then_finalize(migrated_db: aiosqlite.Connection) -> None:
    tracer = await _seed_run(migrated_db)
    adapter = _ScriptedAdapter([
        SqlRoundAction(action="query", sql="SELECT count(*) AS n FROM paper_content",
                       answer=None, papers=[]),
        SqlRoundAction(action="finalize", sql=None, answer="You have 5 papers.",
                       papers=[SqlPaperPick(paper_content_id=10, reason="match")]),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[5]]}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
    ))
    out = "".join(x for x in items if isinstance(x, str))
    assert "You have 5 papers." in out
    assert reg.query_calls == 1
    # Two react rounds traced.
    async with migrated_db.execute(
        "SELECT count(*) FROM tool_calls WHERE run_id = 1 AND tool = 'sql:react'"
    ) as cur:
        assert (await cur.fetchone())[0] == 2
    # The second round saw the first round's rows in query_results.
    assert "5" in str(adapter.calls[1]["query_results"])


# ---------------------------------------------------------------------------
# 3. Cap / force-finalize: adapter ALWAYS queries → at most 4 sql.query; the
#    4th (must_finalize) round must NOT run a 5th query; loop coerces a finalize.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_forces_finalize_after_max_rounds(migrated_db: aiosqlite.Connection) -> None:
    tracer = await _seed_run(migrated_db)
    adapter = _ScriptedAdapter([
        SqlRoundAction(action="query", sql="SELECT 1", answer=None, papers=[]),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[1]]}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
    ))
    # max_rounds = 4: rounds 1-3 each run a query; round 4 is must_finalize so
    # it does NOT run a query even though the model returned action=query.
    assert reg.query_calls <= 4
    assert reg.query_calls == 3
    # Four model rounds were traced (the loop ran to the cap).
    async with migrated_db.execute(
        "SELECT count(*) FROM tool_calls WHERE run_id = 1 AND tool = 'sql:react'"
    ) as cur:
        assert (await cur.fetchone())[0] == 4
    # The must_finalize flag was true on the last round.
    assert adapter.calls[-1]["must_finalize"] in (True, "True", "YES", "yes")
    # The loop still terminates and yields some answer text (coerced finalize).
    assert any(isinstance(x, str) for x in items)


# ---------------------------------------------------------------------------
# 4. Rejected / error query: round 1's query is rejected → loop does NOT crash,
#    appends the error to context, finalizes on a later round.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_query_does_not_crash_then_finalizes(
    migrated_db: aiosqlite.Connection,
) -> None:
    tracer = await _seed_run(migrated_db)
    adapter = _ScriptedAdapter([
        SqlRoundAction(action="query", sql="DROP TABLE papers", answer=None, papers=[]),
        SqlRoundAction(action="finalize", sql=None,
                       answer="I could not run that; here's what I know.", papers=[]),
    ])
    reg = _QueryRegistry([{"error": "rejected", "reason": "not allowed"}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
    ))
    out = "".join(x for x in items if isinstance(x, str))
    assert "could not run" in out
    # The rejection was recorded with status='rejected' (via _mcp_call).
    async with migrated_db.execute(
        "SELECT status FROM tool_calls WHERE run_id = 1 AND tool = 'sql.query'"
    ) as cur:
        statuses = [r[0] for r in await cur.fetchall()]
    assert any(s == "rejected" for s in statuses)
    # The error reached the model's round-2 context so it could refine.
    assert "not allowed" in str(adapter.calls[1]["query_results"]) or "rejected" in str(
        adapter.calls[1]["query_results"]
    )


# ---------------------------------------------------------------------------
# 5. Progressive tracing: with emit_tool_steps the describe + query steps
#    surface as ToolStepYield records before the answer tokens.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emits_tool_steps_before_answer(migrated_db: aiosqlite.Connection) -> None:
    tracer = await _seed_run(migrated_db)
    adapter = _ScriptedAdapter([
        SqlRoundAction(action="query", sql="SELECT count(*) AS n FROM paper_content",
                       answer=None, papers=[]),
        SqlRoundAction(action="finalize", sql=None, answer="You have 3 papers.", papers=[]),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[3]]}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        emit_tool_steps=True,
    ))
    first_token = next((i for i, x in enumerate(items) if isinstance(x, str)), len(items))
    tools_before = {
        x.record["tool"] for x in items[:first_token] if isinstance(x, ToolStepYield)
    }
    assert "sql.describe" in tools_before
    assert "sql.query" in tools_before


# ---------------------------------------------------------------------------
# Task 4 — curated library cards from the finalize picks.
# ---------------------------------------------------------------------------


async def _seed_paper_content(
    conn: aiosqlite.Connection, pcid: int, *, title: str, year: int | None
) -> None:
    await conn.execute(
        "INSERT INTO paper_content "
        "(id, content_key, kind, arxiv_id, title, year, source_path, "
        " source_dir_path, html_path) "
        "VALUES (?, ?, 'arxiv', ?, ?, ?, '/src', '/dir', '/html')",
        (pcid, f"ck{pcid}", f"2400.{pcid:05d}", title, year),
    )


async def _add_to_session(
    conn: aiosqlite.Connection, pcid: int, *, session_id: int = 1
) -> None:
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id) VALUES (?, ?)",
        (session_id, pcid),
    )


@pytest.mark.asyncio
async def test_finalize_emits_curated_cards_before_answer(
    migrated_db: aiosqlite.Connection,
) -> None:
    tracer = await _seed_run(migrated_db)
    # Two existing papers: 10 is in the session, 20 is not.
    await _seed_paper_content(migrated_db, 10, title="Alpha Paper", year=2021)
    await _seed_paper_content(migrated_db, 20, title="Beta Paper", year=2022)
    await _add_to_session(migrated_db, 10)
    await migrated_db.commit()

    adapter = _ScriptedAdapter([
        SqlRoundAction(
            action="finalize", sql=None, answer="Two relevant papers.",
            papers=[
                SqlPaperPick(paper_content_id=10, reason="alpha matches"),
                SqlPaperPick(paper_content_id=20, reason="beta matches"),
            ],
        ),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[2]]}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        conn=migrated_db,
    ))

    yields = [x for x in items if isinstance(x, SearchResultsYield)]
    assert len(yields) == 1
    cands = yields[0].candidates
    # Pick order preserved, library:<id> ids, reason carried, title/year from DB.
    assert [c.paper_id for c in cands] == ["library:10", "library:20"]
    assert [c.reason for c in cands] == ["alpha matches", "beta matches"]
    assert [c.title for c in cands] == ["Alpha Paper", "Beta Paper"]
    assert [c.year for c in cands] == [2021, 2022]
    assert [c.already_in_session for c in cands] == [True, False]
    assert all(c.finalize is False for c in cands)

    # The card is emitted BEFORE the first answer token.
    card_idx = next(i for i, x in enumerate(items) if isinstance(x, SearchResultsYield))
    first_token = next(i for i, x in enumerate(items) if isinstance(x, str))
    assert card_idx < first_token
    out = "".join(x for x in items if isinstance(x, str))
    assert "Two relevant papers." in out


@pytest.mark.asyncio
async def test_finalize_aggregate_emits_no_cards(
    migrated_db: aiosqlite.Connection,
) -> None:
    tracer = await _seed_run(migrated_db)
    adapter = _ScriptedAdapter([
        SqlRoundAction(action="finalize", sql=None, answer="You have 7 papers.", papers=[]),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[7]]}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        conn=migrated_db,
    ))
    assert not any(isinstance(x, SearchResultsYield) for x in items)
    out = "".join(x for x in items if isinstance(x, str))
    assert "You have 7 papers." in out


@pytest.mark.asyncio
async def test_finalize_skips_hallucinated_pcid(
    migrated_db: aiosqlite.Connection,
) -> None:
    tracer = await _seed_run(migrated_db)
    await _seed_paper_content(migrated_db, 10, title="Alpha Paper", year=2021)
    await migrated_db.commit()
    adapter = _ScriptedAdapter([
        SqlRoundAction(
            action="finalize", sql=None, answer="Here is the real one.",
            papers=[
                SqlPaperPick(paper_content_id=999, reason="hallucinated"),
                SqlPaperPick(paper_content_id=10, reason="real"),
            ],
        ),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[1]]}])
    items = await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        conn=migrated_db,
    ))
    yields = [x for x in items if isinstance(x, SearchResultsYield)]
    assert len(yields) == 1
    cands = yields[0].candidates
    assert [c.paper_id for c in cands] == ["library:10"]


@pytest.mark.asyncio
async def test_active_memory_recall_reaches_model(
    migrated_db: aiosqlite.Connection,
) -> None:
    """FR-10: a seeded ACTIVE memory with recall_enabled reaches the model's
    round-1 ``question`` variable (restores the recall coverage Task 5 drops)."""
    tracer = await _seed_run(migrated_db)
    await migrated_db.execute(
        "INSERT INTO memories (scope, session_id, content, status) "
        "VALUES ('session', 1, 'Always respond in Japanese.', 'active')"
    )
    await migrated_db.commit()
    adapter = _ScriptedAdapter([
        SqlRoundAction(action="finalize", sql=None, answer="はい。", papers=[]),
    ])
    reg = _QueryRegistry([{"columns": ["n"], "rows": [[0]]}])
    await _drain(sql_agent_stream(
        _state(), adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        conn=migrated_db, recall_enabled=True,
    ))
    assert "Always respond in Japanese." in str(adapter.calls[0]["question"])
