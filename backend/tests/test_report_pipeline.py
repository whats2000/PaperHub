from typing import Any

import pytest

from paperhub.agents.report_pipeline import (
    coherence_pass,
    draft_frame,
    edit_preamble_block,
    edit_title_block,
    narrate_talk,
    revise_tex,
    understand_paper,
)
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import (
    FrameDraft,
    OutlineSlide,
    PaperBrief,
    TalkOutline,
)
from paperhub.tracing.tracer import Tracer


@pytest.mark.parametrize("slot", ["slides_edit_title/v1", "slides_edit_preamble/v1"])
def test_edit_block_prompt_formats_with_brace_heavy_page_block(slot: str) -> None:
    """The page-1 block fed to these prompts is full of literal LaTeX braces
    (\\begin{document}, \\begin{frame}{...}). The adapter renders the USER
    template via str.format(**vars), so the template must NOT carry unescaped
    literal braces of its own (they belong in the system block). Rendering with
    a brace-heavy page_block must not raise KeyError/IndexError."""
    tmpl = PromptRegistry().get(slot).user_template
    page_block = (
        "\\documentclass{beamer}\n\\title{T}\n\\begin{document}\n"
        "\\begin{frame}[plain]\\titlepage\\end{frame}"
    )
    rendered = tmpl.format(
        page_block=page_block, instruction="do x", response_language="English"
    )
    assert "\\begin{document}" in rendered and "do x" in rendered


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


# --------------------------------------------------------------------------
# F4.2: edit_title_block + edit_preamble_block
# --------------------------------------------------------------------------
class _StreamAdapter:
    def __init__(self) -> None:
        self.slot: str | None = None

    def stream(self, *, slot: str, variables: dict[str, object], model: str):  # type: ignore[no-untyped-def]
        self.slot = slot

        async def g():
            yield "```latex\n" + str(variables["page_block"]).replace("T", "X") + "\n```"

        return g()


@pytest.mark.asyncio
async def test_edit_title_block_uses_slot_and_strips_fences(fake_tracer: Tracer) -> None:
    a = _StreamAdapter()
    out = await edit_title_block(
        adapter=a,
        tracer=fake_tracer,
        model="m",
        page_block="\\title{T}\n\\begin{document}\n\\begin{frame}[plain]\\titlepage\\end{frame}",
        instruction="rename",
        response_language="English",
    )
    assert a.slot == "slides_edit_title/v1"
    assert "```" not in out and "\\title{X}" in out


@pytest.mark.asyncio
async def test_edit_preamble_block_uses_slot(fake_tracer: Tracer) -> None:
    a = _StreamAdapter()
    out = await edit_preamble_block(
        adapter=a,
        tracer=fake_tracer,
        model="m",
        page_block="\\usetheme{default}\n\\begin{document}\n\\begin{frame}[plain]\\titlepage\\end{frame}",
        instruction="dark theme",
        response_language="English",
    )
    assert a.slot == "slides_edit_preamble/v1"
    assert "```" not in out
