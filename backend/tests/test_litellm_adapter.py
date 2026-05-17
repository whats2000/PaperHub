from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import RoutingDecision


async def test_registry_loads_versioned_slot() -> None:
    reg = PromptRegistry()
    slot = reg.get("router/v1")
    assert slot.system.strip().startswith("You are PaperHub's intent router")
    assert "{user_message}" in slot.user_template


async def test_structured_output_parses_into_model() -> None:
    adapter: LlmAdapter = LiteLlmAdapter()
    decision = await adapter.structured(
        slot="router/v1",
        variables={"user_message": "Find recent papers on MoE routing"},
        response_model=RoutingDecision,
        model="gpt-4o-mini",
        mock_response='{"intent":"paper_search","model_tier":"small",'
                      '"confidence":0.91,"reasoning":"asks to find papers"}',
    )
    assert decision.intent == "paper_search"
    assert 0 <= decision.confidence <= 1


async def test_stream_yields_tokens() -> None:
    adapter: LlmAdapter = LiteLlmAdapter()
    chunks: list[str] = []
    async for token in adapter.stream(
        slot="chitchat/v1",
        variables={"user_message": "hi"},
        model="gpt-4o-mini",
        mock_response="Hello there!",
    ):
        chunks.append(token)
    assert "".join(chunks) == "Hello there!"
