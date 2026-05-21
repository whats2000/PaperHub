from collections.abc import AsyncIterator
from typing import Any

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
    async with tracer.step(agent="chitchat", tool="generate", model=model) as step:
        step.record_args({"user_message": user_message, "response_language": language})
        collected: list[str] = []
        async for token in adapter.stream(
            slot="chitchat/v1",
            variables={"user_message": user_message, "response_language": language},
            model=model,
            history=history,
            **adapter_kwargs,
        ):
            collected.append(token)
            yield token
        step.record_result({"length": sum(len(c) for c in collected)})
