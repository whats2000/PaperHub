"""Tests for author_note and edit_frame pipeline functions (Plan F4, Task 8).

The _Stream stub mirrors the real LlmAdapter.stream contract: stream(...) is an
async generator function (not a coroutine), so callers do:
    async for tok in adapter.stream(...):
which matches the existing coherence_pass / revise_tex consumption pattern.
"""
from typing import Any

import pytest

from paperhub.agents.report_pipeline import author_note, edit_frame


class _Stream:
    def __init__(self, toks: list[str]) -> None:
        self._t = toks

    async def structured(self, **kw: Any) -> Any: ...

    def stream(self, **kw: Any):
        async def g():
            for t in self._t:
                yield t

        return g()


@pytest.mark.asyncio
async def test_author_note_returns_text(fake_tracer) -> None:
    out = await author_note(
        adapter=_Stream(["講稿：", "這張投影片說明..."]),
        tracer=fake_tracer,
        model="m",
        frame_tex="\\begin{frame}{方法}\\end{frame}",
        existing_note=None,
        instruction=None,
        note_language="Traditional Chinese",
    )
    assert "這張投影片" in out


@pytest.mark.asyncio
async def test_edit_frame_rewrites_block(fake_tracer) -> None:
    out = await edit_frame(
        adapter=_Stream(["\\begin{frame}{A concise}\\end{frame}"]),
        tracer=fake_tracer,
        model="m",
        frame_tex="\\begin{frame}{A}\\begin{itemize}\\item x\\end{itemize}\\end{frame}",
        instruction="make it more concise",
        response_language="English",
    )
    assert "A concise" in out
