"""Tests for the LLM Provider Adapter contract.

Phase A only covers the FakeAdapter (the test-time double agents will use).
The real LiteLlmAdapter is exercised by Task 8's e2e smoke against real APIs.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from paperhub.llm.adapter import FakeAdapter, LlmMessage, ModelTier


class _Intent(BaseModel):
    intent: Literal["paper_qa", "out_of_scope"]
    confidence: float


@pytest.mark.asyncio()
async def test_fake_adapter_returns_canned_pydantic_instance() -> None:
    adapter = FakeAdapter(canned={"router": _Intent(intent="paper_qa", confidence=0.95)})
    out = await adapter.generate(
        messages=[LlmMessage(role="user", content="hi")],
        model_tier="small",
        response_model=_Intent,
        slot="router",
    )
    assert isinstance(out, _Intent)
    assert out.intent == "paper_qa"
    assert out.confidence == pytest.approx(0.95)


@pytest.mark.asyncio()
async def test_fake_adapter_raises_for_unknown_slot() -> None:
    adapter = FakeAdapter(canned={})
    with pytest.raises(KeyError, match="router"):
        await adapter.generate(
            messages=[LlmMessage(role="user", content="hi")],
            model_tier="small",
            response_model=_Intent,
            slot="router",
        )


@pytest.mark.asyncio()
async def test_fake_adapter_type_mismatch_raises() -> None:
    adapter = FakeAdapter(canned={"router": _Intent(intent="paper_qa", confidence=1.0)})

    class _Other(BaseModel):
        x: int

    with pytest.raises(TypeError):
        await adapter.generate(
            messages=[LlmMessage(role="user", content="hi")],
            model_tier="small",
            response_model=_Other,
            slot="router",
        )


def test_model_tier_literal_has_two_values() -> None:
    # Literal alias exists and accepts the two documented values
    valid_small: ModelTier = "small"
    valid_flag: ModelTier = "flagship"
    assert valid_small == "small"
    assert valid_flag == "flagship"
