import aiosqlite

from paperhub.agents.chitchat import chitchat_stream
from paperhub.agents.state import AgentState
from paperhub.agents.stubs import stub_response
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


async def test_chitchat_stream_yields_tokens(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1, "user_message": "hi",
    }
    chunks: list[str] = []
    async for token in chitchat_stream(
        state, adapter=LiteLlmAdapter(), tracer=tracer,
        model="gpt-4o-mini", mock_response="Hello!",
    ):
        chunks.append(token)
    assert "".join(chunks) == "Hello!"
    async with migrated_db.execute(
        "SELECT agent, tool, status FROM tool_calls"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("chitchat", "generate", "ok")]


async def test_chitchat_stream_uses_history(
    migrated_db: aiosqlite.Connection,
) -> None:
    """History is threaded through to the adapter so prior turns reach the LLM."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1,
        "user_message": "So what question did I just ask?",
        "history": [
            {"role": "user", "content": "1+1=?"},
            {"role": "assistant", "content": "1+1 is 2!"},
        ],
    }
    chunks: list[str] = []
    async for token in chitchat_stream(
        state, adapter=LiteLlmAdapter(), tracer=tracer,
        model="gpt-4o-mini",
        mock_response="You asked about 1+1",
    ):
        chunks.append(token)
    response = "".join(chunks)
    assert "1+1" in response


async def test_chitchat_uses_effective_query(
    migrated_db: aiosqlite.Connection,
) -> None:
    """When effective_query is set (router resolved an anaphora brief), chitchat
    must act on the resolved brief rather than the raw user_message."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1,
        "user_message": "go on",
        "effective_query": "explain flow matching more",
    }
    async for _ in chitchat_stream(
        state, adapter=LiteLlmAdapter(), tracer=tracer,
        model="gpt-4o-mini", mock_response="ok",
    ):
        pass
    async with migrated_db.execute(
        "SELECT args_redacted_json FROM tool_calls WHERE run_id=1 AND tool='generate'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "explain flow matching more" in row[0]


async def test_stub_returns_not_implemented_message() -> None:
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1, "user_message": "x",
    }
    response = await stub_response(state, intent="paper_qa")
    assert "paper_qa" in response
    assert "not yet wired" in response.lower() or "not yet implemented" in response.lower()
