from typing import Any

import aiosqlite

from paperhub.agents.state import AgentState
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import RoutingDecision
from paperhub.tracing.tracer import Tracer


async def router_node(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection | None = None,
    **adapter_kwargs: Any,
) -> AgentState:
    user_message = state["user_message"]
    history = state.get("history") or []
    target_conn = conn if conn is not None else tracer.connection

    # Surface the session's enabled-ref count to the classifier. If the user
    # asks a paper_qa-style question but no refs are attached yet, the prompt
    # rule rewrites the intent to paper_search so the system helps them find
    # papers instead of dead-ending with "No references are enabled".
    session_id = state.get("session_id")
    enabled_refs_count = 0
    if session_id is not None:
        async with target_conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = ? AND enabled = 1",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            enabled_refs_count = int(row[0])

    async with tracer.step(agent="router", tool="classify", model=model) as step:
        step.record_args(
            {"user_message": user_message, "enabled_refs_count": enabled_refs_count},
        )
        decision = await adapter.structured(
            slot="router/v1",
            variables={
                "user_message": user_message,
                "enabled_refs_count": enabled_refs_count,
            },
            response_model=RoutingDecision,
            model=model,
            history=history,
            **adapter_kwargs,
        )
        step.record_result(decision.model_dump())
    await target_conn.execute(
        "UPDATE runs SET routing_decision_json = ? WHERE id = ?",
        (decision.model_dump_json(), state["run_id"]),
    )
    await target_conn.commit()
    return {
        **state,
        "routing_decision": decision,
        "effective_query": decision.resolved_query or user_message,
        "response_language": decision.response_language,
    }
