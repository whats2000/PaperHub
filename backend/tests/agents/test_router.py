"""Tests for the binary Phase A router."""

from __future__ import annotations

import pytest

from paperhub.agents.router import BinaryRoutingDecision, Router
from paperhub.llm.adapter import FakeAdapter
from paperhub.llm.prompts import PromptRegistry


@pytest.fixture()
def prompts() -> PromptRegistry:
    return PromptRegistry.load_default()


@pytest.mark.asyncio
async def test_router_classifies_paper_qa(prompts: PromptRegistry) -> None:
    canned = BinaryRoutingDecision(
        intent="paper_qa",
        confidence=0.9,
        model_tier="small",
        reasoning="User is asking about paper content.",
    )
    adapter = FakeAdapter({"router": canned})
    router = Router(adapter, prompts)

    decision = await router.classify("What is the main contribution of the paper?")

    assert decision.intent == "paper_qa"
    assert decision.confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_router_classifies_chitchat(prompts: PromptRegistry) -> None:
    canned = BinaryRoutingDecision(
        intent="chitchat",
        confidence=0.95,
        model_tier="small",
        reasoning="User is asking about the weather — off-topic.",
    )
    adapter = FakeAdapter({"router": canned})
    router = Router(adapter, prompts)

    decision = await router.classify("What's the weather like today?")

    assert decision.intent == "chitchat"
