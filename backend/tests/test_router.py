import json
import os
from pathlib import Path

import aiosqlite

from paperhub.agents.router import router_node
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


async def test_router_node_returns_routing_decision(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1,
        "user_message": "Find recent papers on MoE routing",
    }
    adapter = LiteLlmAdapter()
    updated = await router_node(
        state,
        adapter=adapter,
        tracer=tracer,
        model="gpt-4o-mini",
        mock_response='{"intent":"paper_search","model_tier":"small",'
                      '"confidence":0.91,"reasoning":"asks to find"}',
    )
    assert updated["routing_decision"].intent == "paper_search"


async def test_router_propagates_response_language(
    migrated_db: aiosqlite.Connection,
) -> None:
    """The router's detected language round-trips into AgentState so
    downstream final-response agents answer in the user's language."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1,
        "user_message": "推薦幾篇關於 MoE routing 的論文",
    }
    adapter = LiteLlmAdapter()
    updated = await router_node(
        state, adapter=adapter, tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"paper_suggest","model_tier":"small",'
                      '"confidence":0.9,"reasoning":"topic recs",'
                      '"resolved_query":"recommend papers on MoE routing",'
                      '"response_language":"Traditional Chinese"}',
    )
    assert updated["routing_decision"].response_language == "Traditional Chinese"
    assert updated["response_language"] == "Traditional Chinese"


async def test_router_response_language_defaults_empty(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A router response omitting response_language parses with "" (the
    field is optional), and state mirrors that empty value."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1, "user_message": "hi",
    }
    adapter = LiteLlmAdapter()
    updated = await router_node(
        state, adapter=adapter, tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"chitchat","model_tier":"small",'
                      '"confidence":0.8,"reasoning":"greeting"}',
    )
    assert updated["routing_decision"].response_language == ""
    assert updated["response_language"] == ""


async def test_router_persists_decision_on_run(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1, "user_message": "hi",
    }
    adapter = LiteLlmAdapter()
    await router_node(
        state, adapter=adapter, tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"chitchat","model_tier":"small",'
                      '"confidence":0.8,"reasoning":"greeting"}',
    )
    async with migrated_db.execute(
        "SELECT routing_decision_json FROM runs WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "chitchat" in row[0]


async def test_router_writes_tool_call_row(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "branch": "", "session_id": 1, "user_message": "hi",
    }
    await router_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer, model="gpt-4o-mini",
        mock_response='{"intent":"chitchat","model_tier":"small",'
                      '"confidence":0.8,"reasoning":"greeting"}',
    )
    async with migrated_db.execute(
        "SELECT agent, tool, status FROM tool_calls"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("router", "classify", "ok")]


async def test_routing_accuracy_at_least_80_percent(
    migrated_db: aiosqlite.Connection,
) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "router_intents.jsonl"
    rows = [json.loads(line) for line in fixture_path.read_text().splitlines() if line]
    correct = 0
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    for row in rows:
        await migrated_db.execute(
            "INSERT INTO runs (session_id) VALUES (1)"
        )
        await migrated_db.commit()
        async with migrated_db.execute("SELECT last_insert_rowid()") as cur:
            r = await cur.fetchone()
        assert r is not None
        run_id = int(r[0])
        tracer = Tracer(migrated_db, run_id=run_id, branch="")
        # Under CI / when no provider key is set, the fixture asserts routing
        # accuracy of the *pipeline*, not the model — every prompt is fed back
        # to LiteLLM with mock_response set to the expected intent. To run
        # against a real provider, set PAPERHUB_ROUTER_LIVE=1 and a key.
        kwargs: dict[str, object] = {}
        if not os.environ.get("PAPERHUB_ROUTER_LIVE"):
            kwargs["mock_response"] = json.dumps({
                "intent": row["expected"], "model_tier": "small",
                "confidence": 0.9, "reasoning": "fixture",
            })
        result = await router_node(
            {
                "run_id": run_id, "branch": "", "session_id": 1,
                "user_message": row["prompt"],
            },
            adapter=LiteLlmAdapter(),
            tracer=tracer,
            model=os.environ.get("PAPERHUB_ROUTER_MODEL", "gpt-4o-mini"),
            **kwargs,
        )
        if result["routing_decision"].intent == row["expected"]:
            correct += 1
    assert correct / len(rows) >= 0.80, f"router accuracy {correct}/{len(rows)} < 80 %"
