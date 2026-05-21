import aiosqlite
import pytest

from paperhub.agents.sql_agent import sql_agent_stream
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
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


# ---------------------------------------------------------------------------
# Fix 3 — self-repair path test
# ---------------------------------------------------------------------------


class _RepairRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._query_n = 0

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return ["papers"]
        if name == "sql.describe":
            return [{"name": "id", "type": "INTEGER"}]
        if name == "sql.query":
            self._query_n += 1
            if self._query_n == 1:
                return {"error": "execution failed"}
            return {"columns": ["n"], "rows": [[5]]}
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_sql_agent_self_repair(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    adapter = LiteLlmAdapter()
    reg = _RepairRegistry()
    state: AgentState = {
        "run_id": 1, "session_id": 1, "user_message": "how many papers?",
        "effective_query": "how many papers?", "response_language": "English",
    }
    tokens: list[str] = []
    async for tok in sql_agent_stream(
        state, adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        planner_mock="SELECT count(*) AS n FROM papers",
        repair_mock="SELECT count(*) AS n FROM papers WHERE 1=1",
        answer_mock="You have 5 papers.\n```sql\nSELECT count(*) AS n FROM papers WHERE 1=1\n```",
    ):
        tokens.append(tok)

    out = "".join(tokens)
    assert "5" in out
    assert sum(1 for c in reg.calls if c[0] == "sql.query") == 2
    assert any(c[0] == "sql.describe" for c in reg.calls)


# ---------------------------------------------------------------------------
# Fix 4 — rejected path writes status='rejected'
# ---------------------------------------------------------------------------


class _RejectedRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return ["papers"]
        if name == "sql.describe":
            return [{"name": "id", "type": "INTEGER"}]
        if name == "sql.query":
            return {"error": "rejected", "reason": "not allowed"}
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_sql_agent_rejected_path_writes_status(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    adapter = LiteLlmAdapter()
    reg = _RejectedRegistry()
    state: AgentState = {
        "run_id": 1, "session_id": 1, "user_message": "drop everything",
        "effective_query": "drop everything", "response_language": "English",
    }
    tokens: list[str] = []
    async for tok in sql_agent_stream(
        state, adapter=adapter, tracer=tracer, registry=reg,
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        planner_mock="DROP TABLE papers",
        repair_mock="DROP TABLE papers",
        answer_mock="Rejected.\n```sql\nDROP TABLE papers\n```",
    ):
        tokens.append(tok)

    async with migrated_db.execute(
        "SELECT status FROM tool_calls WHERE run_id = 1 AND tool = 'sql.query'"
    ) as cur:
        statuses = [r[0] for r in await cur.fetchall()]
    assert any(s == "rejected" for s in statuses)


# ---------------------------------------------------------------------------
# Fix 5 — language interpolation test (proves the yaml fix)
# ---------------------------------------------------------------------------


def test_sql_answer_prompt_interpolates_language() -> None:
    slot = PromptRegistry().get("sql_answer/v1")
    rendered = slot.user_template.format(
        response_language="Traditional Chinese",
        question="q",
        sql="s",
        columns="[]",
        rows="[]",
    )
    assert "Traditional Chinese" in rendered
    assert "{response_language}" not in rendered
