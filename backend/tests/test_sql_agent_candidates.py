"""E1 Task 1 — SQL Agent yields ``library:<id>`` candidates for paper-shaped
results.

When a ``library_stats`` query's executed SELECT includes a
``paper_content_id`` column, ``sql_agent_stream`` must yield exactly one
``SearchResultsYield`` (one ``library:<id>`` candidate per row) BEFORE the
answer-token stream. When the result has no ``paper_content_id`` column
(aggregate query), it must yield NO ``SearchResultsYield``.
"""
import aiosqlite
import pytest

from paperhub.agents.research import SearchResultsYield
from paperhub.agents.sql_agent import sql_agent_stream
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


class _PaperShapedRegistry:
    """``sql.query`` returns a paper-shaped SELECT: a ``paper_content_id``
    column plus ``title``/``year`` for two rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return ["papers", "paper_content"]
        if name == "sql.describe":
            return {"columns": [{"name": "id", "type": "INTEGER"}]}
        if name == "sql.query":
            return {
                "columns": ["paper_content_id", "title", "year"],
                "rows": [
                    [10, "Attention Is All You Need", 2017],
                    [20, "Diffusion Models", 2020],
                ],
            }
        raise AssertionError(name)


class _AggregateRegistry:
    """``sql.query`` returns an aggregate result with NO ``paper_content_id``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return ["papers", "paper_content"]
        if name == "sql.describe":
            return {"columns": [{"name": "id", "type": "INTEGER"}]}
        if name == "sql.query":
            return {"columns": ["year", "n"], "rows": [[2017, 3], [2020, 5]]}
        raise AssertionError(name)


async def _seed_papers(conn: aiosqlite.Connection) -> None:
    """Two paper_content rows (10, 20); only pcid 10 is a member of session 1."""
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
    for pcid, title, year in ((10, "Attention Is All You Need", 2017),
                              (20, "Diffusion Models", 2020)):
        await conn.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, arxiv_id, title, year, source_path, "
            " source_dir_path, html_path) "
            "VALUES (?, ?, 'arxiv', ?, ?, ?, '', '', '')",
            (pcid, f"key-{pcid}", f"arx-{pcid}", title, year),
        )
    # Only pcid 10 is attached to session 1.
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id) VALUES (1, 10)",
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_sql_agent_yields_library_candidates_for_paper_shaped_result(
    migrated_db: aiosqlite.Connection,
) -> None:
    await _seed_papers(migrated_db)
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "session_id": 1,
        "user_message": "list my papers", "effective_query": "list my papers",
        "response_language": "English",
    }
    items: list = []
    async for item in sql_agent_stream(
        state, adapter=LiteLlmAdapter(), tracer=tracer,
        registry=_PaperShapedRegistry(),
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        planner_mock="SELECT paper_content_id, title, year FROM paper_content",
        answer_mock="Here are your papers.\n```sql\nSELECT 1\n```",
        conn=migrated_db,
    ):
        items.append(item)

    yields = [x for x in items if isinstance(x, SearchResultsYield)]
    assert len(yields) == 1, f"expected exactly one SearchResultsYield, got {len(yields)}"
    candidates = yields[0].candidates
    assert [c.paper_id for c in candidates] == ["library:10", "library:20"]
    assert [c.title for c in candidates] == [
        "Attention Is All You Need", "Diffusion Models",
    ]
    assert [c.year for c in candidates] == [2017, 2020]
    # pcid 10 is in the session; pcid 20 is not.
    assert [c.already_in_session for c in candidates] == [True, False]
    assert all(c.finalize is False for c in candidates)

    # The candidates yield arrives BEFORE the first answer token.
    first_token = next(
        (i for i, x in enumerate(items) if isinstance(x, str)), len(items),
    )
    first_yield = next(
        i for i, x in enumerate(items) if isinstance(x, SearchResultsYield)
    )
    assert first_yield < first_token, "candidates must precede the answer stream"


@pytest.mark.asyncio
async def test_sql_agent_no_candidates_for_aggregate_result(
    migrated_db: aiosqlite.Connection,
) -> None:
    await _seed_papers(migrated_db)
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "session_id": 1,
        "user_message": "how many papers per year?",
        "effective_query": "how many papers per year?",
        "response_language": "English",
    }
    items: list = []
    async for item in sql_agent_stream(
        state, adapter=LiteLlmAdapter(), tracer=tracer,
        registry=_AggregateRegistry(),
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        planner_mock="SELECT year, count(*) AS n FROM paper_content GROUP BY year",
        answer_mock="2017: 3, 2020: 5\n```sql\nSELECT 1\n```",
        conn=migrated_db,
    ):
        items.append(item)

    yields = [x for x in items if isinstance(x, SearchResultsYield)]
    assert yields == [], "aggregate result must yield no SearchResultsYield"
    assert any(isinstance(x, str) for x in items), "answer tokens still stream"
