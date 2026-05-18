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


async def test_structured_with_history_builds_correct_messages() -> None:
    """structured() with history produces a messages array of len 2 + len(history)."""
    history = [
        {"role": "user", "content": "1+1=?"},
        {"role": "assistant", "content": "1+1 is 2!"},
    ]
    adapter = LiteLlmAdapter()
    # Capture the messages that would be sent by patching _messages
    captured: list[list[dict[str, str]]] = []
    original_messages = adapter._messages  # noqa: SLF001

    def patched_messages(
        slot: str,
        variables: dict,
        hist: list | None = None,
    ) -> list[dict[str, str]]:
        result = original_messages(slot, variables, hist)
        captured.append(result)
        return result

    adapter._messages = patched_messages  # type: ignore[method-assign]  # noqa: SLF001

    await adapter.structured(
        slot="router/v1",
        variables={"user_message": "So what did I ask?"},
        response_model=RoutingDecision,
        model="gpt-4o-mini",
        history=history,
        mock_response='{"intent":"chitchat","model_tier":"small",'
                      '"confidence":0.9,"reasoning":"follow-up"}',
    )

    assert len(captured) == 1
    messages = captured[0]
    # system + 2 history turns + user = 4
    assert len(messages) == 2 + len(history)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "1+1=?"
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "user"
