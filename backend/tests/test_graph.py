import aiosqlite

from paperhub.agents.graph import GraphDeps, build_graph
from paperhub.agents.router import router_node
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


async def test_router_sets_effective_query_from_resolved(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": "推薦幾篇"}
    out = await router_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"paper_search","model_tier":"small","confidence":1.0,'
                      '"reasoning":"r","resolved_query":"recommend discrete diffusion distillation papers"}',
    )
    assert out["effective_query"] == "recommend discrete diffusion distillation papers"


async def test_router_effective_query_falls_back_to_raw(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": "hello"}
    out = await router_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"chitchat","model_tier":"small","confidence":0.85,"reasoning":"greeting"}',
    )
    assert out["effective_query"] == "hello"


async def test_clarify_path(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    deps = GraphDeps(
        adapter=LiteLlmAdapter(), tracer=tracer,
        router_model="gpt-4o-mini", chitchat_model="gpt-4o-mini",
        router_mock='{"intent":"clarify","model_tier":"small","confidence":0.4,'
                    '"reasoning":"no topic yet","resolved_query":"Which research topic would you like papers on?"}',
    )
    graph = build_graph(deps)
    state: AgentState = {"run_id": 1, "branch": "", "session_id": 1, "user_message": "推薦幾篇"}
    result = await graph.ainvoke(state)
    assert result["routing_decision"].intent == "clarify"
    assert result["final_response"] == "Which research topic would you like papers on?"

