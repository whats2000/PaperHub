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
    dec = DeckCommand(action="edit_notes", target_scope="all", note_language="Traditional Chinese")
    out = await classify_deck_command(
        adapter=_A(dec), tracer=fake_tracer, model="m",
        instruction="把講稿變成繁體中文", current_view_page=3, deck_outline="1. Intro",
    )
    assert out.action == "edit_notes" and out.note_language == "Traditional Chinese"


@pytest.mark.asyncio
async def test_edit_current_page(fake_tracer) -> None:
    dec = DeckCommand(action="edit_slides", target_scope="current")
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
