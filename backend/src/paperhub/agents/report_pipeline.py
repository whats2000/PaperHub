"""Traced LLM-calling units for the Report Agent subgraph.

Three pipeline functions — plan_deck, generate_section, generate_notes —
each wrapped in a Tracer step per the agent-flow observability policy
(CLAUDE.md). Every step records enough state to reconstruct the agent
context entirely from the DB alone.
"""
from __future__ import annotations

import re

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import PlannedSection, SlidePlan
from paperhub.tracing.tracer import Tracer


async def plan_deck(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    papers_block: str,
    response_language: str,
    memory_context: str,
) -> SlidePlan:
    """Call the LLM to produce a structured SlidePlan from the papers block.

    Slot: ``slides_plan/v1``.  Traced as ``report:plan``.
    """
    async with tracer.step(agent="report", tool="report:plan", model=model) as step:
        step.record_args({"papers_block_len": len(papers_block)})
        plan = await adapter.structured(
            slot="slides_plan/v1",
            variables={
                "papers_block": papers_block,
                "response_language": response_language or "the user's language",
                "memory_context": memory_context,
            },
            response_model=SlidePlan,
            model=model,
        )
        step.record_result(
            {
                "title": plan.title,
                "sections": [
                    {
                        "title": s.title,
                        "paper_content_ids": s.paper_content_ids,
                    }
                    for s in plan.sections
                ],
            }
        )
    return plan


async def generate_section(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    deck_title: str,
    section: PlannedSection,
    chunks_block: str,
    response_language: str,
    memory_context: str,
    chunk_ids: list[int] | None = None,
) -> str:
    """Stream-generate a single Beamer ``\\begin{frame}...\\end{frame}`` block.

    Slot: ``slides_section/v1``.  Traced as ``report:section``.
    Records the section title, chunk IDs used, and the rendered frame text.
    """
    async with tracer.step(agent="report", tool="report:section", model=model) as step:
        step.record_args(
            {"section_title": section.title, "chunk_ids": chunk_ids or []}
        )
        tokens: list[str] = []
        async for tok in adapter.stream(
            slot="slides_section/v1",
            variables={
                "deck_title": deck_title,
                "section_title": section.title,
                "section_intent": section.intent,
                "chunks_block": chunks_block,
                "response_language": response_language or "the user's language",
                "memory_context": memory_context,
            },
            model=model,
        ):
            tokens.append(tok)
        frame = "".join(tokens).strip()
        step.record_result({"section_title": section.title, "frame": frame})
    return frame


async def generate_notes(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    beamer_code: str,
    response_language: str,
) -> dict[str, str]:
    """Stream-generate speaker notes and parse ``[SLIDE N]`` blocks.

    Slot: ``slides_notes/v1``.  Traced as ``report:notes``.
    Returns a mapping of slide-number string → note text.
    Records the sorted list of note page keys written.
    """
    async with tracer.step(agent="report", tool="report:notes", model=model) as step:
        step.record_args({"beamer_len": len(beamer_code)})
        tokens: list[str] = []
        async for tok in adapter.stream(
            slot="slides_notes/v1",
            variables={
                "beamer_code": beamer_code,
                "response_language": response_language or "the user's language",
            },
            model=model,
        ):
            tokens.append(tok)
        raw = "".join(tokens)
        notes: dict[str, str] = {}
        for m in re.finditer(
            r"\[SLIDE\s+(\d+)\]\s*\n?(.*?)(?=\[SLIDE\s+\d+\]|\Z)",
            raw,
            re.DOTALL,
        ):
            notes[m.group(1)] = m.group(2).strip()
        step.record_result({"note_pages": sorted(notes.keys())})
    return notes
