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
    async with tracer.step(agent="router", tool="classify", model=model) as step:
        step.record_args({"user_message": user_message})
        decision = await adapter.structured(
            slot="router/v1",
            variables={"user_message": user_message},
            response_model=RoutingDecision,
            model=model,
            history=history,
            **adapter_kwargs,
        )
        step.record_result(decision.model_dump())
    target_conn = conn if conn is not None else tracer._conn  # noqa: SLF001
    await target_conn.execute(
        "UPDATE runs SET routing_decision_json = ? WHERE id = ?",
        (decision.model_dump_json(), state["run_id"]),
    )
    await target_conn.commit()
    return {**state, "routing_decision": decision}
