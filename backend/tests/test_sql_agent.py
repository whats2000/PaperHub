import json

import aiosqlite
import pytest

from paperhub.agents.sql_agent import _normalize_mcp_result, sql_agent_stream
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


# ---------------------------------------------------------------------------
# Fix 6 — _normalize_mcp_result contract + JSON-string registry coverage
# ---------------------------------------------------------------------------


def test_normalize_mcp_result_passthrough_dict() -> None:
    """Dicts are returned unchanged (not a string)."""
    d = {"columns": ["n"], "rows": [[3]]}
    assert _normalize_mcp_result(d) is d


def test_normalize_mcp_result_passthrough_list() -> None:
    """Lists are returned unchanged (not a string)."""
    lst = ["papers", "paper_content"]
    assert _normalize_mcp_result(lst) is lst


def test_normalize_mcp_result_json_string_dict() -> None:
    """A JSON-encoded dict string is parsed into a dict."""
    raw = '{"columns":["n"],"rows":[[3]]}'
    result = _normalize_mcp_result(raw)
    assert result == {"columns": ["n"], "rows": [[3]]}


def test_normalize_mcp_result_json_string_list() -> None:
    """A JSON-encoded list string is parsed into a list."""
    raw = '["papers"]'
    result = _normalize_mcp_result(raw)
    assert result == ["papers"]


def test_normalize_mcp_result_non_json_string_returned_unchanged() -> None:
    """A plain (non-JSON) string that doesn't start with { or [ is returned as-is.

    _normalize_mcp_result only attempts json.loads when the stripped string
    starts with '{' or '['.  Anything else is returned verbatim — callers
    can detect it as neither dict nor list.
    """
    raw = "oops"
    assert _normalize_mcp_result(raw) == "oops"


# ---------------------------------------------------------------------------
# Registry that returns JSON *strings* (mirrors the real sql FastMCP server)
# ---------------------------------------------------------------------------


class _JsonStringRegistry:
    """Returns all results as JSON-serialised strings, like the real MCP transport."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return json.dumps(["papers", "paper_content"])
        if name == "sql.describe":
            return json.dumps([{"name": "session_id", "type": "INTEGER"}])
        if name == "sql.query":
            return json.dumps({"columns": ["n"], "rows": [[3]]})
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_sql_agent_json_string_registry(migrated_db: aiosqlite.Connection) -> None:
    """sql_agent_stream works correctly when the MCP registry returns JSON strings."""
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
        state, adapter=adapter, tracer=tracer, registry=_JsonStringRegistry(),
        planner_model="gpt-4o-mini", answer_model="gpt-4o-mini",
        planner_mock="SELECT count(*) AS n FROM papers",
        answer_mock="You have 3 papers.\n```sql\nSELECT count(*) AS n FROM papers\n```",
    ):
        tokens.append(tok)
    out = "".join(tokens)
    assert "3 papers" in out
    async with migrated_db.execute(
        "SELECT tool FROM tool_calls WHERE run_id = 1 AND tool LIKE 'sql.%'"
    ) as cur:
        tools = {r[0] for r in await cur.fetchall()}
    assert "sql.query" in tools


# ---------------------------------------------------------------------------
# Registry that returns a JSON-encoded rejected error (mirrors real transport)
# ---------------------------------------------------------------------------


class _JsonStringRejectedRegistry:
    """Returns the rejected error as a JSON string, as the real MCP transport does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return json.dumps(["papers"])
        if name == "sql.describe":
            return json.dumps([{"name": "id", "type": "INTEGER"}])
        if name == "sql.query":
            return json.dumps({"error": "rejected", "reason": "not allowed"})
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_sql_agent_json_string_rejected_writes_status(
    migrated_db: aiosqlite.Connection,
) -> None:
    """When the MCP returns a JSON-string rejection, tool_calls.status='rejected' is written."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    adapter = LiteLlmAdapter()
    reg = _JsonStringRejectedRegistry()
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
