from typing import Any

import pytest

from paperhub.agents.report_pipeline import classify_deck_command
from paperhub.models.domain import DeckCommand


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
