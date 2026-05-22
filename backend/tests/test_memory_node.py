import aiosqlite
import pytest

from paperhub.agents.memory_node import memory_node
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


class _FakeRegistry:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def call(self, name: str, args: dict) -> object:
        from paperhub.agents import memory_tools as mt

        if name == "memory.add":
            mid = await mt.add_memory(
                self.conn,
                session_id=1,
                content=args["content"],
                scope=args["scope"],
            )
            return {"id": mid}
        if name == "memory.recall":
            hits = await mt.recall_memories(
                self.conn, session_id=1, query=args["query"], scope="both"
            )
            return [{"id": h.id, "scope": h.scope, "content": h.content} for h in hits]
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_memory_node_add_persists_and_confirms(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "remember I'm comparing MoE routing papers",
        "effective_query": "remember I'm comparing MoE routing papers",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock='{"op":"add","scope":"session","content":"comparing MoE routing papers","target":"","confirmation":"Noted — I will remember that."}',
    )
    assert "final_response" in out and out["final_response"]
    async with migrated_db.execute("SELECT content FROM memories") as cur:
        rows = await cur.fetchall()
    assert rows and "MoE routing" in rows[0][0]


@pytest.mark.asyncio
async def test_memory_node_unknown_op_returns_fallback(
    migrated_db: aiosqlite.Connection,
) -> None:
    """When the LLM returns an unrecognised op, the node should return a graceful message."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "do something weird",
        "effective_query": "do something weird",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock='{"op":"unknown","scope":"session","content":"x","target":"","confirmation":"ok"}',
    )
    assert "final_response" in out
    assert "rephrase" in out["final_response"].lower()


@pytest.mark.asyncio
async def test_memory_node_confirmation_in_state(
    migrated_db: aiosqlite.Connection,
) -> None:
    """The confirmation string from the LLM op JSON is forwarded as final_response."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "remember I prefer dark mode",
        "effective_query": "remember I prefer dark mode",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock='{"op":"add","scope":"session","content":"prefers dark mode","target":"","confirmation":"Got it, I will remember that you prefer dark mode."}',
    )
    assert out["final_response"] == "Got it, I will remember that you prefer dark mode."
