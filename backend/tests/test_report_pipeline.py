from typing import Any

import pytest

from paperhub.agents.report_pipeline import (
    NoteSegments,
    coherence_pass,
    draft_slide,
    finalize_notes,
    narrate_talk,
    revise_tex,
    understand_paper,
)
from paperhub.models.domain import (
    OutlineSlide,
    PaperBrief,
    SlideDraft,
    TalkOutline,
)
from paperhub.tracing.tracer import Tracer


class _StructAdapter:
    def __init__(self, obj: Any = None, tokens: list[str] | None = None) -> None:
        self._obj, self._tokens = obj, tokens or []

    async def structured(self, **kw: Any) -> Any:
        return self._obj

    def stream(self, **kw: Any):
        async def g():
            for t in self._tokens:
                yield t
        return g()


# --------------------------------------------------------------------------
# F3 PhD-grade pipeline functions.
# --------------------------------------------------------------------------
def _brief() -> PaperBrief:
    return PaperBrief(
        paper_id=7,
        contribution="A new attention mechanism.",
        method="Scaled dot-product attention.",
        key_results=["SOTA BLEU on WMT14"],
        key_figure_keys=["fig_arch"],
        key_equations=["softmax(QK^T/sqrt(d))V"],
    )


async def _step_tools(tracer: Tracer) -> list[str]:
    """Return the tool names recorded on tool_calls for the tracer's run."""
    async with tracer.connection.execute(
        "SELECT tool FROM tool_calls WHERE run_id = ? ORDER BY step_index",
        (tracer.run_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_understand_paper_returns_brief_and_traces(fake_tracer: Tracer) -> None:
    out = await understand_paper(
        paper_block="abstract + sections ...",
        adapter=_StructAdapter(obj=_brief()),
        tracer=fake_tracer, model="m", response_language="English",
    )
    assert isinstance(out, PaperBrief)
    assert out.contribution == "A new attention mechanism."
    tools = await _step_tools(fake_tracer)
    assert "report:understand" in tools


@pytest.mark.asyncio
async def test_narrate_talk_returns_outline(fake_tracer: Tracer) -> None:
    outline = TalkOutline(
        title="Attention",
        slides=[
            OutlineSlide(title="Motivation", goal="why", key_points=["p1"], paper_ids=[7]),
            OutlineSlide(title="Method", goal="how", key_points=["p2"], figure_key="fig_arch", chunk_ids=[3]),
        ],
    )
    out = await narrate_talk(
        briefs_block="brief 7 ...",
        figure_inventory="fig_arch",
        adapter=_StructAdapter(obj=outline),
        tracer=fake_tracer, model="m", response_language="English",
    )
    assert isinstance(out, TalkOutline)
    assert out.title == "Attention"
    assert out.slides[1].figure_key == "fig_arch"
    tools = await _step_tools(fake_tracer)
    assert "report:narrate" in tools


@pytest.mark.asyncio
async def test_draft_slide_returns_draft(fake_tracer: Tracer) -> None:
    draft = SlideDraft(frame="\\begin{frame}{Method}\\end{frame}", note="Walk through the equation.")
    out = await draft_slide(
        deck_title="Attention",
        slide=OutlineSlide(title="Method", goal="how", key_points=["p2"], figure_key="fig_arch"),
        assigned_figure="fig_arch",
        assigned_equation="softmax(QK^T/sqrt(d))V",
        chunks_block="chunk text",
        adapter=_StructAdapter(obj=draft),
        tracer=fake_tracer, model="m", response_language="English",
    )
    assert isinstance(out, SlideDraft)
    assert "Method" in out.frame
    tools = await _step_tools(fake_tracer)
    assert "report:draft" in tools


@pytest.mark.asyncio
async def test_coherence_pass_splits_frames(fake_tracer: Tracer) -> None:
    # Stub returns the two frames concatenated (with surrounding prose).
    stub = (
        "Here is the polished deck:\n"
        "\\begin{frame}{A}\ncontent A\n\\end{frame}\n\n"
        "\\begin{frame}{B}\ncontent B\n\\end{frame}\n"
        "Done."
    )
    out = await coherence_pass(
        frames=["\\begin{frame}{A}\\end{frame}", "\\begin{frame}{B}\\end{frame}"],
        adapter=_StructAdapter(tokens=[stub]),
        tracer=fake_tracer, model="m", response_language="English",
    )
    assert len(out) == 2
    assert out[0].startswith("\\begin{frame}{A}")
    assert out[1].startswith("\\begin{frame}{B}")
    tools = await _step_tools(fake_tracer)
    assert "report:coherence" in tools


@pytest.mark.asyncio
async def test_coherence_pass_falls_back_on_empty(fake_tracer: Tracer) -> None:
    frames = ["\\begin{frame}{A}\\end{frame}", "\\begin{frame}{B}\\end{frame}"]
    out = await coherence_pass(
        frames=frames,
        adapter=_StructAdapter(tokens=["no frames here"]),
        tracer=fake_tracer, model="m", response_language="English",
    )
    assert out == frames


@pytest.mark.asyncio
async def test_revise_tex_strips_fences(fake_tracer: Tracer) -> None:
    corrected = "\\documentclass{beamer}\n\\begin{document}\\end{document}"
    out = await revise_tex(
        pdflatex_log="! Overfull \\hbox ...",
        tex="\\documentclass{beamer}",
        adapter=_StructAdapter(tokens=["```latex\n", corrected, "\n```"]),
        tracer=fake_tracer, model="m",
    )
    assert out == corrected
    assert "```" not in out
    tools = await _step_tools(fake_tracer)
    assert "report:revise" in tools


# --------------------------------------------------------------------------
# F3 T9 — layout-aware speaker notes (split a frame's note per PDF page).
# --------------------------------------------------------------------------
def _no_continued(notes: dict[str, str]) -> None:
    assert all(v != "(continued)" for v in notes.values())


@pytest.mark.asyncio
async def test_finalize_notes_splits_two_page_slide(fake_tracer: Tracer) -> None:
    # \maketitle (page 1, title) + two frames sharing the frametitle "Method"
    # → group_logical_slides = [[1 title], [2, 3]]. One draft → the 2-page group.
    final_tex = (
        "\\maketitle\n"
        "\\begin{frame}{Method}\ncontent one\n\\end{frame}\n"
        "\\begin{frame}{Method}\ncontent two\n\\end{frame}\n"
    )
    d = SlideDraft(frame="f", note="The full method note covering both pages.")
    notes = await finalize_notes(
        drafts=[d],
        final_tex=final_tex,
        page_count=3,
        adapter=_StructAdapter(obj=NoteSegments(segments=["seg A", "seg B"])),
        tracer=fake_tracer,
        model="m",
        response_language="English",
    )
    assert set(notes.keys()) == {"1", "2", "3"}
    assert notes["2"] == "seg A"
    assert notes["3"] == "seg B"
    assert notes["2"] != notes["3"]
    # The title page (1) is NOT "(continued)" — empty or a short opener.
    assert notes["1"] != "(continued)"
    _no_continued(notes)
    tools = await _step_tools(fake_tracer)
    assert "report:notes_finalize" in tools


@pytest.mark.asyncio
async def test_finalize_notes_single_page_verbatim_no_llm(fake_tracer: Tracer) -> None:
    final_tex = "\\begin{frame}{Method}\ncontent\n\\end{frame}\n"
    d = SlideDraft(frame="f", note="note one")

    class _Boom:
        async def structured(self, **kw: Any) -> Any:
            raise AssertionError("must not call the LLM for a single-page slide")

        def stream(self, **kw: Any):  # type: ignore[no-untyped-def]
            raise AssertionError("must not stream")

    notes = await finalize_notes(
        drafts=[d],
        final_tex=final_tex,
        page_count=1,
        adapter=_Boom(),
        tracer=fake_tracer,
        model="m",
        response_language="English",
    )
    assert notes == {"1": "note one"}
    _no_continued(notes)


@pytest.mark.asyncio
async def test_finalize_notes_fallback_on_split_error(fake_tracer: Tracer) -> None:
    """Regression: if the note-split adapter call raises (any exception), finalize_notes
    must NOT propagate the error. Instead it falls back to _deterministic_split so
    the deck is still delivered with real per-page speech (never '(continued)')."""
    final_tex = (
        "\\maketitle\n"
        "\\begin{frame}{Method}\ncontent one\n\\end{frame}\n"
        "\\begin{frame}{Method}\ncontent two\n\\end{frame}\n"
    )
    # Two sentences so the deterministic splitter can assign one per page.
    d = SlideDraft(
        frame="f",
        note="First sentence covers the setup. Second sentence covers the result.",
    )

    class _SplitErrorAdapter:
        """Returns normally for all calls EXCEPT the note-split slot (NoteSegments),
        which raises RuntimeError — simulating the pre-fix KeyError crash."""

        async def structured(self, **kw: Any) -> Any:
            if kw.get("response_model") is NoteSegments:
                raise RuntimeError("simulated note-split failure")
            raise AssertionError(f"unexpected structured call: {kw}")

        def stream(self, **kw: Any):  # type: ignore[no-untyped-def]
            raise AssertionError("stream must not be called")

    notes = await finalize_notes(
        drafts=[d],
        final_tex=final_tex,
        page_count=3,
        adapter=_SplitErrorAdapter(),
        tracer=fake_tracer,
        model="m",
        response_language="English",
    )
    # All three pages must be present.
    assert set(notes.keys()) == {"1", "2", "3"}
    # Title page (1) must not be "(continued)".
    assert notes["1"] != "(continued)"
    # The two content pages must be non-empty, non-"(continued)", and distinct
    # (the two sentences land on different pages).
    assert notes["2"] and notes["2"] != "(continued)"
    assert notes["3"] and notes["3"] != "(continued)"
    assert notes["2"] != notes["3"]
    _no_continued(notes)
    # Tracing must still have fired despite the error.
    tools = await _step_tools(fake_tracer)
    assert "report:notes_finalize" in tools


@pytest.mark.asyncio
async def test_finalize_notes_degrades_more_pages_than_drafts(
    fake_tracer: Tracer,
) -> None:
    # Three distinct content frames but only one draft → degrade, no crash.
    final_tex = (
        "\\begin{frame}{A}\na\n\\end{frame}\n"
        "\\begin{frame}{B}\nb\n\\end{frame}\n"
        "\\begin{frame}{C}\nc\n\\end{frame}\n"
    )
    d = SlideDraft(frame="f", note="only note")
    notes = await finalize_notes(
        drafts=[d],
        final_tex=final_tex,
        page_count=3,
        adapter=_StructAdapter(obj=NoteSegments(segments=["x"])),
        tracer=fake_tracer,
        model="m",
        response_language="English",
    )
    assert set(notes.keys()) == {"1", "2", "3"}
    _no_continued(notes)
    assert all(notes[k] for k in notes)  # every page non-empty
