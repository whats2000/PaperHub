import aiosqlite
import pytest

from paperhub.agents.sql_agent import sql_agent_stream
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


class _FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return ["papers", "paper_content"]
        if name == "sql.describe":
            return [{"name": "session_id", "type": "INTEGER"}]
        if name == "sql.query":
            return {"columns": ["n"], "rows": [[3]]}
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_sql_agent_emits_sql_runs_and_answers(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    adapter = LiteLlmAdapter()
    state: AgentState = {
        "run_id": 1, "session_id": 1, "user_message": "how many papers do I have?",
        "effective_query": "how many papers do I have?", "response_language": "English",
    }
    tokens: list[str] = []
    async for tok in sql_agent_stream(
        state, adapter=adapter, tracer=tracer, registry=_FakeRegistry(),
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        planner_mock="SELECT count(*) AS n FROM papers",
        answer_mock="You have 3 papers.\n```sql\nSELECT count(*) AS n FROM papers\n```",
    ):
        tokens.append(tok)
    out = "".join(tokens)
    assert "3 papers" in out
    assert "```sql" in out
    async with migrated_db.execute(
        "SELECT tool FROM tool_calls WHERE run_id = 1 AND tool LIKE 'sql.%'"
    ) as cur:
        tools = {r[0] for r in await cur.fetchall()}
    assert "sql.query" in tools
