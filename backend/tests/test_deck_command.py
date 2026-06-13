from typing import Any

import pytest

from paperhub.agents.report_pipeline import (
    classify_deck_command,
    detect_slide_language,
)
from paperhub.models.domain import DeckCommand, TargetLanguage


class _A:
    def __init__(self, obj: Any) -> None: self._o = obj
    async def structured(self, **kw: Any) -> Any: return self._o
    def stream(self, **kw: Any): ...


@pytest.mark.asyncio
async def test_relanguage_notes(fake_tracer) -> None:
    dec = DeckCommand(action="edit_notes", target_scope="all", target_page=None, note_language="Traditional Chinese")
    out = await classify_deck_command(
        adapter=_A(dec), tracer=fake_tracer, model="m",
        instruction="把講稿變成繁體中文", current_view_page=3, deck_outline="1. Intro",
    )
    assert out.action == "edit_notes" and out.note_language == "Traditional Chinese"


@pytest.mark.asyncio
async def test_edit_current_page(fake_tracer) -> None:
    dec = DeckCommand(action="edit_slides", target_scope="current", target_page=None)
    out = await classify_deck_command(
        adapter=_A(dec), tracer=fake_tracer, model="m",
        instruction="make this slide more concise", current_view_page=3,
        deck_outline="1. Intro\n3. Method",
    )
    assert out.action == "edit_slides" and out.target_scope == "current"


@pytest.mark.asyncio
async def test_detect_slide_language_explicit(fake_tracer) -> None:
    lang = await detect_slide_language(
        adapter=_A(TargetLanguage(language="English")), tracer=fake_tracer,
        model="m", instruction="能幫我把簡報換成英文嗎",
    )
    assert lang == "English"


@pytest.mark.asyncio
async def test_detect_slide_language_none_when_unspecified(fake_tracer) -> None:
    # No explicit deck-language request → None → caller falls back to
    # response_language (the chat language).
    lang = await detect_slide_language(
        adapter=_A(TargetLanguage(language=None)), tracer=fake_tracer,
        model="m", instruction="幫我做一份關於這篇論文的簡報",
    )
    assert lang is None


@pytest.mark.asyncio
async def test_edit_title_action(fake_tracer) -> None:
    dec = DeckCommand(action="edit_title", target_scope="all", target_page=None)
    out = await classify_deck_command(
        adapter=_A(dec), tracer=fake_tracer, model="m",
        instruction="edit the title page authors", current_view_page=1,
        deck_outline="1. Intro",
    )
    assert out.action == "edit_title"


@pytest.mark.asyncio
async def test_edit_preamble_action(fake_tracer) -> None:
    dec = DeckCommand(action="edit_preamble", target_scope="all", target_page=None)
    out = await classify_deck_command(
        adapter=_A(dec), tracer=fake_tracer, model="m",
        instruction="make the whole deck use a dark blue theme",
        current_view_page=2, deck_outline="1. Intro\n2. Method",
    )
    assert out.action == "edit_preamble"


def test_deck_command_prompt_lists_qa_and_attached_rules() -> None:
    from paperhub.llm.prompts.registry import PromptRegistry
    p = PromptRegistry().get("slides_deck_command/v1")
    assert '"qa"' in p.system
    assert "explain" in p.system.lower()
    assert "SLIDE_ATTACHED" in p.system or "slide_attached" in p.system
    assert "{slide_attached}" in p.user_template


def test_deck_command_target_page_is_required_in_schema() -> None:
    # F1 root cause: with a default, Gemini's responseSchema treats target_page
    # as droppable and the model omits it even for "slide 4" — losing the page
    # number. The language-agnostic fix is to make it REQUIRED so the model must
    # emit it (in any language); no per-language few-shot examples are needed.
    schema = DeckCommand.model_json_schema()
    assert "target_page" in schema["required"]
    # The description stays language-agnostic — no enumerated examples.
    desc = schema["properties"]["target_page"]["description"]
    assert "any language" in desc
    assert "第三" not in desc and "slide 4" not in desc
