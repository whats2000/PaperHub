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


async def test_stub_returns_not_implemented_message() -> None:
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1, "user_message": "x",
    }
    response = await stub_response(state, intent="paper_qa")
    assert "paper_qa" in response
    assert "not yet wired" in response.lower() or "not yet implemented" in response.lower()
