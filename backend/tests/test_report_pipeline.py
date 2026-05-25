from typing import Any

import pytest

from paperhub.agents.report_pipeline import (
    coherence_pass,
    draft_frame,
    narrate_talk,
    revise_tex,
    understand_paper,
)
from paperhub.models.domain import (
    FrameDraft,
    OutlineSlide,
    PaperBrief,
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
# F4: frame-only draft
# --------------------------------------------------------------------------
class _StructFrameA:
    def __init__(self, obj: Any) -> None:
        self._o = obj

    async def structured(self, **kw: Any) -> Any:
        return self._o

    def stream(self, **kw: Any):  # type: ignore[no-untyped-def]
        ...


@pytest.mark.asyncio
async def test_draft_frame_returns_frame_only(fake_tracer: Tracer) -> None:
    fd = FrameDraft(frame="\\begin{frame}{A}\\end{frame}")
    out = await draft_frame(
        deck_title="T",
        slide=OutlineSlide(title="A", goal="g", key_points=["k"]),
        assigned_figure=None,
        assigned_equation=None,
        chunks_block="(none)",
        adapter=_StructFrameA(fd),
        tracer=fake_tracer,
        model="m",
        response_language="English",
    )
    assert out.frame == "\\begin{frame}{A}\\end{frame}"
    assert not hasattr(out, "note")
