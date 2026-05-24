"""Traced LLM-calling units for the Report Agent subgraph.

Three pipeline functions — plan_deck, generate_section, generate_notes —
each wrapped in a Tracer step per the agent-flow observability policy
(CLAUDE.md). Every step records enough state to reconstruct the agent
context entirely from the DB alone.
"""
from __future__ import annotations

import re

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import (
    OutlineSlide,
    PaperBrief,
    PlannedSection,
    SlideDraft,
    SlidePlan,
    TalkOutline,
)
from paperhub.tracing.tracer import Tracer

# Matches one ``\begin{frame}...\end{frame}`` block (non-greedy, dotall) so the
# coherence pass can split a re-emitted multi-frame document back into frames.
_FRAME_RE = re.compile(r"\\begin\{frame\}.*?\\end\{frame\}", re.DOTALL)
# Strip a leading/trailing markdown code fence (```latex ... ```), tolerating an
# optional language tag on the opening fence.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")


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
    available_figures: list[str] | None = None,
) -> str:
    """Stream-generate a single Beamer ``\\begin{frame}...\\end{frame}`` block.

    Slot: ``slides_section/v1``.  Traced as ``report:section``.
    Records the section title, chunk IDs used, and the rendered frame text.

    ``available_figures`` is the list of real figure stems on disk (from
    :func:`~paperhub.pipelines.slide_pipeline.figures.collect_figures`).
    It is forwarded to the prompt so the LLM only references real files.
    """
    fig_list = sorted(available_figures) if available_figures else []
    async with tracer.step(agent="report", tool="report:section", model=model) as step:
        step.record_args(
            {
                "section_title": section.title,
                "chunk_ids": chunk_ids or [],
                "available_figures": fig_list,
            }
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
                "available_figures": "\n".join(fig_list) or "(none)",
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


# --------------------------------------------------------------------------
# F3 PhD-grade slide pipeline (SRS v2.19).
#
# understand_paper → narrate_talk → draft_slide → coherence_pass →
# (assemble/compile in the graph) → revise_tex (compile loop) →
# finalize_notes (deterministic).
# --------------------------------------------------------------------------
def _split_frames(tex: str) -> list[str]:
    """Split a Beamer document/string into its ``\\begin{frame}..\\end{frame}``
    blocks. Surrounding prose / preamble is dropped. Returns [] if none match."""
    return [m.group(0).strip() for m in _FRAME_RE.finditer(tex)]


def _strip_fences(text: str) -> str:
    """Remove a wrapping markdown code fence from an LLM stream, if present."""
    out = text.strip()
    if out.startswith("```"):
        out = _FENCE_RE.sub("", out)
        out = _FENCE_RE.sub("", out)
    return out.strip()


async def understand_paper(
    *,
    paper_block: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    response_language: str,
    **kw: object,
) -> PaperBrief:
    """Distil one paper into a structured :class:`PaperBrief`.

    Slot ``slides_understand/v1``.  Traced as ``report:understand``; records the
    brief's fields + the paper-block length so the stage is reconstructable.
    """
    async with tracer.step(agent="report", tool="report:understand", model=model) as step:
        step.record_args({"paper_block_len": len(paper_block)})
        brief = await adapter.structured(
            slot="slides_understand/v1",
            variables={
                "paper_block": paper_block,
                "response_language": response_language or "the user's language",
            },
            response_model=PaperBrief,
            model=model,
        )
        step.record_result(
            {
                "paper_id": brief.paper_id,
                "contribution": brief.contribution,
                "method": brief.method,
                "key_results": brief.key_results,
                "key_figure_keys": brief.key_figure_keys,
                "key_equations": brief.key_equations,
            }
        )
    return brief


async def narrate_talk(
    *,
    briefs_block: str,
    figure_inventory: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    response_language: str,
    memory_context: str = "",
    **kw: object,
) -> TalkOutline:
    """Compose a cross-paper :class:`TalkOutline` from the per-paper briefs.

    Slot ``slides_narrate/v1``.  Traced as ``report:narrate``; records the deck
    title + each slide's title and figure/equation/chunk/paper pointers.
    """
    async with tracer.step(agent="report", tool="report:narrate", model=model) as step:
        step.record_args(
            {
                "briefs_block_len": len(briefs_block),
                "figure_inventory": figure_inventory,
            }
        )
        outline = await adapter.structured(
            slot="slides_narrate/v1",
            variables={
                "briefs_block": briefs_block,
                "figure_inventory": figure_inventory,
                "response_language": response_language or "the user's language",
                "memory_context": memory_context,
            },
            response_model=TalkOutline,
            model=model,
        )
        step.record_result(
            {
                "title": outline.title,
                "slides": [
                    {
                        "title": s.title,
                        "figure_key": s.figure_key,
                        "equation": s.equation,
                        "chunk_ids": s.chunk_ids,
                        "paper_ids": s.paper_ids,
                    }
                    for s in outline.slides
                ],
            }
        )
    return outline


async def draft_slide(
    *,
    deck_title: str,
    slide: OutlineSlide,
    assigned_figure: str | None,
    assigned_equation: str | None,
    chunks_block: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    response_language: str,
    memory_context: str = "",
    **kw: object,
) -> SlideDraft:
    """Draft one Beamer frame + its speaker note for an outline slide.

    Slot ``slides_draft/v1``.  Traced as ``report:draft``; records the slide
    title, whether a figure/equation was assigned, and the frame/note lengths.
    """
    async with tracer.step(agent="report", tool="report:draft", model=model) as step:
        step.record_args(
            {
                "slide_title": slide.title,
                "has_figure": bool(assigned_figure),
                "has_equation": bool(assigned_equation),
                "chunk_ids": slide.chunk_ids,
            }
        )
        draft = await adapter.structured(
            slot="slides_draft/v1",
            variables={
                "deck_title": deck_title,
                "slide_title": slide.title,
                "slide_goal": slide.goal,
                "key_points": "\n".join(f"- {p}" for p in slide.key_points),
                "assigned_figure": assigned_figure or "(none)",
                "assigned_equation": assigned_equation or "(none)",
                "chunks_block": chunks_block,
                "response_language": response_language or "the user's language",
                "memory_context": memory_context,
            },
            response_model=SlideDraft,
            model=model,
        )
        step.record_result(
            {
                "slide_title": slide.title,
                "had_figure": bool(assigned_figure),
                "had_equation": bool(assigned_equation),
                "frame_len": len(draft.frame),
                "note_len": len(draft.note),
            }
        )
    return draft


async def coherence_pass(
    *,
    frames: list[str],
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    response_language: str,
    **kw: object,
) -> list[str]:
    """Smooth transitions across drafted frames as a whole.

    Slot ``slides_coherence/v1``.  Streams a revised multi-frame document, then
    splits it back into ``\\begin{frame}..\\end{frame}`` blocks. Falls back to
    the input frames if the model returns nothing usable.  Traced as
    ``report:coherence``; records the in/out frame counts.
    """
    async with tracer.step(agent="report", tool="report:coherence", model=model) as step:
        step.record_args({"in_frame_count": len(frames)})
        tokens: list[str] = []
        async for tok in adapter.stream(
            slot="slides_coherence/v1",
            variables={
                "frames_block": "\n\n".join(frames),
                "response_language": response_language or "the user's language",
            },
            model=model,
        ):
            tokens.append(tok)
        out = _split_frames("".join(tokens))
        if not out:
            out = frames
        step.record_result({"in_frame_count": len(frames), "out_frame_count": len(out)})
    return out


async def revise_tex(
    *,
    pdflatex_log: str,
    tex: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    **kw: object,
) -> str:
    """Repair the deck's LaTeX in response to a pdflatex log (compile loop).

    Slot ``slides_revise/v1``.  Streams the corrected document, strips any code
    fences.  Traced as ``report:revise``; records the log length + whether the
    output differs from the input.
    """
    async with tracer.step(agent="report", tool="report:revise", model=model) as step:
        step.record_args({"log_len": len(pdflatex_log)})
        tokens: list[str] = []
        async for tok in adapter.stream(
            slot="slides_revise/v1",
            variables={"pdflatex_log": pdflatex_log, "tex": tex},
            model=model,
        ):
            tokens.append(tok)
        revised = _strip_fences("".join(tokens))
        if not revised:
            revised = tex
        step.record_result({"log_len": len(pdflatex_log), "changed": revised != tex})
    return revised


def finalize_notes(drafts: list[SlideDraft], page_count: int) -> dict[str, str]:
    """Map drafted speaker notes to PDF page numbers (deterministic, no LLM).

    Pages are ``"1".."page_count"``.  If there are fewer drafted notes than
    pages, gap pages get a short fallback note; if more, the surplus is dropped.
    """
    notes: dict[str, str] = {}
    for page in range(1, max(page_count, 0) + 1):
        idx = page - 1
        if idx < len(drafts) and drafts[idx].note.strip():
            notes[str(page)] = drafts[idx].note
        else:
            notes[str(page)] = "(continued)"
    return notes
