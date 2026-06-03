"""Tests for the F4 follow-up LLM-calling units (Plan F4 / F4.5).

The _Adapter stub mirrors the real LlmAdapter contract:
  - ``stream(...)`` is an async generator function (NOT a coroutine), so
    callers do ``async for tok in adapter.stream(...)``; ``revise_tex`` /
    ``edit_frame`` consume it that way.
  - ``structured(...)`` is an awaitable returning a parsed Pydantic model;
    ``author_deck_notes`` calls it.
"""
from typing import Any

import pytest

from paperhub.agents.report_pipeline import author_deck_notes, edit_frame
from paperhub.models.domain import DeckNoteEntry, DeckNotesAuthor


class _Adapter:
    def __init__(
        self,
        *,
        stream_tokens: list[str] | None = None,
        structured_result: Any = None,
    ) -> None:
        self._toks = stream_tokens or []
        self._structured_result = structured_result

    async def structured(self, **_kw: Any) -> Any:
        return self._structured_result

    def stream(self, **_kw: Any):
        async def g():
            for t in self._toks:
                yield t

        return g()


@pytest.mark.asyncio
async def test_author_deck_notes_returns_wanted_only(fake_tracer) -> None:
    # The author returns notes for slide_indices 1 and 3 (the wanted set);
    # the result map filters out blanks and keys by slide_index.
    canned = DeckNotesAuthor(
        notes=[
            DeckNoteEntry(slide_index=1, note="Intro voice for slide 1."),
            DeckNoteEntry(slide_index=3, note="Bridge into the next paper."),
        ]
    )
    out = await author_deck_notes(
        adapter=_Adapter(structured_result=canned),
        tracer=fake_tracer,
        model="m",
        papers=[
            {
                "title": "FASTer",
                "authors": ["Liu", "Chen"],
                "abstract": "Block-wise autoregressive VLA decoding.",
            }
        ],
        frames=[
            (0, 1, "\\begin{frame}{}\\titlepage\\end{frame}"),
            (1, 2, "\\begin{frame}{Intro}\\end{frame}"),
            (3, 4, "\\begin{frame}{Method}\\end{frame}"),
        ],
        existing_notes={2: "Stay-untouched note on slide 2."},
        wanted_indices=[1, 3],
        note_language="English",
    )
    assert set(out.keys()) == {1, 3}
    assert "Intro" in out[1]
    assert "Bridge" in out[3]


@pytest.mark.asyncio
async def test_author_deck_notes_drops_blank_entries(fake_tracer) -> None:
    # An empty-string note should be filtered out — partial output shouldn't
    # land in deck_slides as an authoritative "empty note".
    canned = DeckNotesAuthor(
        notes=[
            DeckNoteEntry(slide_index=1, note="    "),
            DeckNoteEntry(slide_index=2, note="Real note."),
        ]
    )
    out = await author_deck_notes(
        adapter=_Adapter(structured_result=canned),
        tracer=fake_tracer,
        model="m",
        papers=[],
        frames=[(1, 1, "x"), (2, 2, "y")],
        existing_notes={},
        wanted_indices=[1, 2],
        note_language="English",
    )
    assert out == {2: "Real note."}


@pytest.mark.asyncio
async def test_edit_frame_rewrites_block(fake_tracer) -> None:
    out = await edit_frame(
        adapter=_Adapter(stream_tokens=["\\begin{frame}{A concise}\\end{frame}"]),
        tracer=fake_tracer,
        model="m",
        frame_tex="\\begin{frame}{A}\\begin{itemize}\\item x\\end{itemize}\\end{frame}",
        instruction="make it more concise",
        response_language="English",
    )
    assert "A concise" in out
