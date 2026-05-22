from collections.abc import AsyncIterator
from typing import Any

from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


async def chitchat_stream(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    **adapter_kwargs: Any,
) -> AsyncIterator[str]:
    user_message = effective_query(state)
    language = response_language(state)
    history = state.get("history") or []
    # Surface active memories (incl. a standing language preference) so even a
    # casual reply honors them. Unconditional block — an FTS query on "hi"
    # would never match a "respond in Japanese" preference. Best-effort: a
    # missing/closed connection just yields no block.
    memory_context = ""
    conn = getattr(tracer, "connection", None)
    if conn is not None:
        memory_context = await build_active_memory_block(
            conn, session_id=state.get("session_id")
        )
    async with tracer.step(agent="chitchat", tool="generate", model=model) as step:
        step.record_args({
            "user_message": user_message,
            "response_language": language,
            "recall_hit": bool(memory_context),
        })
        collected: list[str] = []
        async for token in adapter.stream(
            slot="chitchat/v1",
            variables={
                "user_message": user_message,
                "response_language": language,
                "memory_context": memory_context,
            },
            model=model,
            history=history,
            **adapter_kwargs,
        ):
            collected.append(token)
            yield token
        step.record_result({"length": sum(len(c) for c in collected)})
