from typing import Any

import pytest

from paperhub.agents.report_pipeline import generate_notes, generate_section, plan_deck
from paperhub.models.domain import PlannedSection, SlidePlan
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


@pytest.mark.asyncio
async def test_plan_deck_returns_plan(fake_tracer: Tracer) -> None:
    plan = SlidePlan(title="T", sections=[PlannedSection(title="Motivation", intent="why", paper_content_ids=[1])])
    out = await plan_deck(
        adapter=_StructAdapter(obj=plan), tracer=fake_tracer, model="m",
        papers_block="...", response_language="English", memory_context="",
    )
    assert out.title == "T"
    assert out.sections[0].title == "Motivation"


@pytest.mark.asyncio
async def test_generate_section_streams_frame(fake_tracer: Tracer) -> None:
    frame = await generate_section(
        adapter=_StructAdapter(tokens=["\\begin{frame}{Motivation}", "\\end{frame}"]),
        tracer=fake_tracer, model="m", deck_title="T",
        section=PlannedSection(title="Motivation", intent="why", paper_content_ids=[1]),
        chunks_block="chunk text", response_language="English", memory_context="",
        available_figures=["model", "attn"],
    )
    assert "\\begin{frame}{Motivation}" in frame


@pytest.mark.asyncio
async def test_generate_section_passes_available_figures_to_template(fake_tracer: Tracer) -> None:
    """available_figures is forwarded to the adapter as 'available_figures' variable."""
    received: dict[str, Any] = {}

    class _CapturingAdapter:
        def stream(self, *, variables: dict[str, Any], **kw: Any):  # type: ignore[no-untyped-def]
            received.update(variables)
            async def g():  # type: ignore[no-untyped-def]
                yield "\\begin{frame}\\end{frame}"
            return g()

    await generate_section(
        adapter=_CapturingAdapter(), tracer=fake_tracer, model="m", deck_title="T",
        section=PlannedSection(title="S", intent="i", paper_content_ids=[]),
        chunks_block="x", response_language="English", memory_context="",
        available_figures=["fig_a", "fig_b"],
    )
    assert "available_figures" in received
    assert "fig_a" in received["available_figures"]
    assert "fig_b" in received["available_figures"]


@pytest.mark.asyncio
async def test_generate_notes_parses_slide_markers(fake_tracer: Tracer) -> None:
    notes = await generate_notes(
        adapter=_StructAdapter(tokens=["[SLIDE 1]\nSay hello.\n", "[SLIDE 2]\nNext point."]),
        tracer=fake_tracer, model="m", beamer_code="...", response_language="English",
    )
    assert notes["1"].startswith("Say hello")
    assert notes["2"].startswith("Next point")
