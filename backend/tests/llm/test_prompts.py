"""Tests for the YAML-driven prompt registry."""

from __future__ import annotations

import pytest

from paperhub.llm.prompts import PromptNotFoundError, PromptRegistry


def test_registry_loads_router_v1() -> None:
    reg = PromptRegistry.load_default()
    rendered = reg.render(slot="router", version="v1", user_message="Hello there")
    assert "PaperHub's task router" in rendered.system
    assert "Hello there" in rendered.user


def test_registry_loads_research_qa_v1() -> None:
    reg = PromptRegistry.load_default()
    rendered = reg.render(
        slot="research_qa",
        version="v1",
        question="What is X?",
        passages="§1, p.1: example.",
    )
    assert "research assistant" in rendered.system
    assert "What is X?" in rendered.user
    assert "§1, p.1" in rendered.user


def test_registry_missing_slot_raises() -> None:
    reg = PromptRegistry.load_default()
    with pytest.raises(PromptNotFoundError):
        reg.render(slot="nonexistent", version="v1", x="y")


def test_registry_missing_version_raises() -> None:
    reg = PromptRegistry.load_default()
    with pytest.raises(PromptNotFoundError):
        reg.render(slot="router", version="v99", user_message="x")


def test_registry_missing_template_var_raises_key_error() -> None:
    reg = PromptRegistry.load_default()
    with pytest.raises(KeyError):
        reg.render(slot="router", version="v1")
