"""F6.1 sl_outline — cross-paper narrative planning stage.

Runs ONCE over all gathered PaperContextBundles (between gather_context and
slide_agent). Produces a DeckOutlineDraft via one structured LLM call, then
resolves it deterministically into a DeckOutline: assigns slide_index by order
and maps each slide's grounding_sections -> chunks.id via SQL (no LLM emits raw
chunk integers). Traced as ``report:outline`` recording the full outline +
dropped sections so a misnarrated deck is diagnosable from the DB alone.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from paperhub.models.slide_domain import (
    DeckOutline,
    DeckOutlineDraft,
    OutlineSlide,
    PaperContextBundle,
)
from paperhub.tracing.tracer import Tracer


def _format_bundles_block(bundles: list[PaperContextBundle]) -> str:
    """Render the bundles for the outline prompt, EXPOSING section names so the
    LLM can cite them in grounding_sections."""
    parts: list[str] = []
    for b in bundles:
        figs = ", ".join(f.key for f in b.key_figures) or "(none)"
        secs = ", ".join(sorted({e.section_name for e in b.section_excerpts})) or "(none)"
        parts.append(
            f"### Paper {b.paper_idx} (paper_id={b.paper_id}): {b.title}\n"
            f"Narrative: {b.narrative_summary}\n"
            f"Figure inventory keys: {figs}\n"
            f"Section names (cite these in grounding_sections): {secs}\n"
        )
    return "\n".join(parts)


async def _chunk_ids_for_sections(
    *, conn: aiosqlite.Connection, paper_content_id: int, sections: list[str]
) -> list[int]:
    """Resolve (paper, section names) -> sorted distinct chunk ids."""
    out: list[int] = []
    for name in sections:
        async with conn.execute(
            "SELECT id FROM chunks WHERE paper_content_id = ? AND section = ? ORDER BY id",
            (paper_content_id, name),
        ) as cur:
            out.extend(int(r[0]) for r in await cur.fetchall())
    return sorted(set(out))


async def run_sl_outline(
    *,
    bundles: list[PaperContextBundle],
    task_description: str,
    response_language: str,
    adapter: Any,  # LlmAdapter — has .structured(...)
    tracer: Tracer,
    model: str,
    conn: aiosqlite.Connection,
) -> DeckOutline:
    known_figs = {f.key for b in bundles for f in b.key_figures}
    async with tracer.step(agent="report", tool="report:outline", model=model) as step:
        step.record_args(
            {
                "task_description": task_description,
                "response_language": response_language,
                "paper_ids": [b.paper_id for b in bundles],
                "n_bundles": len(bundles),
            }
        )
        draft: DeckOutlineDraft = await adapter.structured(
            slot="slides_outline/v1",
            variables={
                "task_description": task_description,
                "response_language": response_language,
                "n_bundles": len(bundles),
                "bundles_block": _format_bundles_block(bundles),
            },
            response_model=DeckOutlineDraft,
            model=model,
        )

        dropped: list[str] = []
        resolved: list[OutlineSlide] = []
        for idx, s in enumerate(draft.slides):
            chunk_ids: list[int] = []
            if s.paper_id is not None and s.grounding_sections:
                chunk_ids = await _chunk_ids_for_sections(
                    conn=conn, paper_content_id=s.paper_id, sections=s.grounding_sections
                )
                for name in s.grounding_sections:
                    hit = await _chunk_ids_for_sections(
                        conn=conn, paper_content_id=s.paper_id, sections=[name]
                    )
                    if not hit:
                        dropped.append(f"{s.paper_id}:{name}")
            figure_key = s.figure_key if s.figure_key in known_figs else None
            resolved.append(
                OutlineSlide(
                    slide_index=idx,
                    goal=s.goal,
                    key_message=s.key_message,
                    transition_from_prev=s.transition_from_prev,
                    paper_id=s.paper_id,
                    figure_key=figure_key,
                    grounding_chunk_ids=chunk_ids,
                )
            )

        outline = DeckOutline(
            talk_title=draft.talk_title,
            audience_intent=draft.audience_intent,
            narrative_arc=draft.narrative_arc,
            slides=resolved,
        )
        step.record_result(
            {
                "talk_title": outline.talk_title,
                "audience_intent": outline.audience_intent,
                "narrative_arc": outline.narrative_arc,
                "n_slides": len(outline.slides),
                "slides": [s.model_dump() for s in outline.slides],
                "dropped_sections": dropped,
            }
        )
    return outline
