"""Traced LLM-calling units for the Report Agent subgraph (Plan F3/F4).

The slide pipeline functions — understand_paper, narrate_talk, draft_frame
(concise frame only), coherence_pass, revise_tex — plus the F4 follow-up
units (classify_deck_command, author_note, edit_frame) are each wrapped in a
Tracer step per the agent-flow observability policy (CLAUDE.md). Every step
records enough state to reconstruct the agent context entirely from the DB
alone. Speaker notes are authored separately by ``author_note`` (the F4 NOTES
flow), NOT generated at deck-create time.
"""
from __future__ import annotations

import re

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import (
    DeckCommand,
    FrameDraft,
    OutlineSlide,
    PaperBrief,
    SlideBudget,
    TalkOutline,
)
from paperhub.tracing.tracer import Tracer

# Matches one ``\begin{frame}...\end{frame}`` block (non-greedy, dotall) so the
# coherence pass can split a re-emitted multi-frame document back into frames.
_FRAME_RE = re.compile(r"\\begin\{frame\}.*?\\end\{frame\}", re.DOTALL)
# Strip a leading/trailing markdown code fence (```latex ... ```), tolerating an
# optional language tag on the opening fence.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")

# Budget extraction patterns (F4 — SRS v2.21).
_SLIDE_RE = re.compile(r"(\d+)\s*(?:slides?|頁|張|投影片)", re.IGNORECASE)
_MIN_RE = re.compile(r"(\d+)[- ]?(?:min(?:ute)?s?|分鐘|分)", re.IGNORECASE)


def parse_slide_budget(text: str) -> SlideBudget:
    """Extract a slide-count budget from the user's request. Explicit slide
    count wins; else minutes × 0.75; else default 15. Clamped to [8, 30]."""
    count: int | None = None
    m = _SLIDE_RE.search(text)
    if m:
        count = int(m.group(1))
    else:
        mm = _MIN_RE.search(text)
        if mm:
            count = round(int(mm.group(1)) * 0.75)
    if count is None:
        count = 15
    count = max(8, min(30, count))
    return SlideBudget(target_slide_count=count, depth="standard")


# --------------------------------------------------------------------------
# F3/F4 PhD-grade slide pipeline (SRS v2.19/v2.21).
#
# understand_paper → narrate_talk → draft_frame (concise frame only) →
# coherence_pass → (assemble/compile in the graph) → revise_tex (compile loop).
# Speaker notes are authored on demand by author_note (F4 NOTES flow), not at
# deck-create time.
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
    target_slide_count: int = 15,
    depth: str = "standard",
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
                "target_slide_count": target_slide_count,
                "depth": depth,
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


async def draft_frame(
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
) -> FrameDraft:
    """Draft one CONCISE Beamer frame (no speaker note) for an outline slide.

    Slot ``slides_draft_frame/v1``.  Traced as ``report:draft``; records the
    slide title, figure/equation pointers, and the frame length. Speaker notes
    are authored separately by the F4 NOTES flow.
    """
    async with tracer.step(agent="report", tool="report:draft", model=model) as step:
        step.record_args(
            {
                "slide_title": slide.title,
                "figure_key": slide.figure_key,
                "chunk_ids": slide.chunk_ids,
            }
        )
        draft = await adapter.structured(
            slot="slides_draft_frame/v1",
            variables={
                "deck_title": deck_title,
                "slide_goal": slide.goal,
                "slide_title": slide.title,
                "key_points": "\n".join(f"- {p}" for p in slide.key_points),
                "assigned_figure": assigned_figure or "",
                "assigned_equation": assigned_equation or "",
                "chunks_block": chunks_block,
                "response_language": response_language or "the user's language",
                "memory_context": memory_context,
            },
            response_model=FrameDraft,
            model=model,
        )
        step.record_result({"frame": draft.frame})
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


# --------------------------------------------------------------------------
# F4: DeckCommand classifier (SRS v2.21).
# --------------------------------------------------------------------------

async def classify_deck_command(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, instruction: str,
    current_view_page: int, deck_outline: str,
) -> DeckCommand:
    """Classify a slides follow-up turn (when a deck already exists) into one
    :class:`DeckCommand` action.  Slot ``slides_deck_command/v1``; traced as
    ``report:deck_command``."""
    async with tracer.step(agent="report", tool="report:deck_command", model=model) as step:
        step.record_args({"instruction": instruction, "current_view_page": current_view_page})
        dec = await adapter.structured(
            slot="slides_deck_command/v1",
            variables={
                "instruction": instruction,
                "current_view_page": current_view_page,
                "deck_outline": deck_outline,
            },
            response_model=DeckCommand,
            model=model,
        )
        step.record_result(dec.model_dump())
    return dec


# --------------------------------------------------------------------------
# F4: Note-author + frame-edit streaming functions (SRS v2.21, Task 8).
# --------------------------------------------------------------------------

async def author_note(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    frame_tex: str,
    existing_note: str | None,
    instruction: str | None,
    note_language: str,
) -> str:
    """Write (or rewrite) the SPEAKER NOTE for one Beamer frame.

    When ``existing_note`` is supplied the model translates / rewrites it per
    ``instruction``; otherwise it authors a fresh note from the frame content.
    Slot ``slides_note_author/v1``.  Streams the note token-by-token; traced as
    ``report:note_author``.
    """
    async with tracer.step(agent="report", tool="report:note_author", model=model) as step:
        step.record_args(
            {"note_language": note_language, "has_existing": existing_note is not None}
        )
        toks: list[str] = []
        async for t in adapter.stream(
            slot="slides_note_author/v1",
            variables={
                "frame_tex": frame_tex,
                "existing_note": existing_note or "(none — author fresh)",
                "instruction": instruction or "(none)",
                "note_language": note_language or "the user's language",
            },
            model=model,
        ):
            toks.append(t)
        out = "".join(toks).strip()
        step.record_result({"note": out})
    return out


async def edit_frame(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    frame_tex: str,
    instruction: str,
    response_language: str,
) -> str:
    """Rewrite ONE Beamer frame per the user's instruction.

    The model returns only the ``\\begin{frame}...\\end{frame}`` block; any
    stray markdown fences are stripped.  Falls back to the original ``frame_tex``
    if the model returns nothing usable.  Slot ``slides_edit_frame/v1``; traced
    as ``report:edit_frame``.
    """
    async with tracer.step(agent="report", tool="report:edit_frame", model=model) as step:
        step.record_args({"old_frame": frame_tex, "instruction": instruction})
        toks: list[str] = []
        async for t in adapter.stream(
            slot="slides_edit_frame/v1",
            variables={
                "frame_tex": frame_tex,
                "instruction": instruction,
                "response_language": response_language or "the user's language",
            },
            model=model,
        ):
            toks.append(t)
        out = "".join(toks).strip()
        if out.startswith("```"):
            out = out.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        step.record_result({"new_frame": out})
    return out or frame_tex
