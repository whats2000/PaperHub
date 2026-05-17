import aiosqlite

from paperhub.agents.graph import GraphDeps, build_graph
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


async def test_chitchat_path(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    deps = GraphDeps(
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        router_model="gpt-4o-mini",
        chitchat_model="gpt-4o-mini",
        router_mock='{"intent":"chitchat","model_tier":"small",'
                    '"confidence":0.85,"reasoning":"greeting"}',
        chitchat_mock="Hi there!",
    )
    graph = build_graph(deps)
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1, "user_message": "hello",
    }
    result = await graph.ainvoke(state)
    assert result["final_response"] == "Hi there!"
    assert result["routing_decision"].intent == "chitchat"


async def test_paper_qa_path_returns_stub(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    deps = GraphDeps(
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        router_model="gpt-4o-mini",
        chitchat_model="gpt-4o-mini",
        router_mock='{"intent":"paper_qa","model_tier":"flagship",'
                    '"confidence":0.93,"reasoning":"asks about a paper"}',
        chitchat_mock="",
    )
    graph = build_graph(deps)
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1,
        "user_message": "explain expert collapse in this paper",
    }
    result = await graph.ainvoke(state)
    assert "paper_qa" in result["final_response"]
    assert "not yet" in result["final_response"].lower()
