"""Traced LLM-calling units for the Report Agent subgraph (Plan F3).

The PhD-grade slide pipeline functions — understand_paper, narrate_talk,
draft_slide, coherence_pass, revise_tex, finalize_notes — each wrapped in a
Tracer step per the agent-flow observability policy (CLAUDE.md). Every step
records enough state to reconstruct the agent context entirely from the DB
alone.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from typing import Any

from pydantic import BaseModel, ConfigDict

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import (
    FrameDraft,
    OutlineSlide,
    PaperBrief,
    SlideBudget,
    SlideDraft,
    TalkOutline,
)
from paperhub.pipelines.slide_pipeline.frame_map import (
    group_logical_slides,
    map_pages_to_slides,
)
from paperhub.tracing.tracer import Tracer


class NoteSegments(BaseModel):
    """K per-page speaker-note segments for one logical slide that the compile
    loop split across K consecutive PDF pages (F3 T9). Produced by the
    ``slides_note_split/v1`` slot."""

    model_config = ConfigDict(extra="forbid")

    segments: list[str]

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


def _frametitle_for_group(pages_map: dict[int, str | None], group: list[int]) -> str:
    """Best-effort frametitle for a content group (for the split prompt)."""
    for p in group:
        title = pages_map.get(p)
        if title:
            return title
    return ""


def _coerce_segments(segments: list[str], k: int, fallback: str) -> list[str]:
    """Make exactly ``k`` non-empty segments, padding (repeat last / fallback)
    or truncating as needed. Never yields ``"(continued)"``."""
    out = [s for s in segments]
    if len(out) > k:
        out = out[:k]
    while len(out) < k:
        out.append(out[-1] if out else fallback)
    return [(s.strip() or fallback or " ") for s in out]


def _deterministic_split(note: str, k: int) -> list[str]:
    """Split ``note`` into ``k`` contiguous sentence-grouped segments — the
    LLM-free fallback when the note-split call fails, so a split frame still
    gets real per-page speech (never ``"(continued)"``)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", note.strip()) if s.strip()]
    if not sentences:
        return [note for _ in range(k)]
    per = max(1, len(sentences) // k)
    segs = [" ".join(sentences[i * per : (i + 1) * per]) for i in range(k - 1)]
    segs.append(" ".join(sentences[(k - 1) * per :]))  # last takes the remainder
    return segs


async def finalize_notes(
    *,
    drafts: list[SlideDraft],
    final_tex: str,
    page_count: int,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    response_language: str = "the user's language",
    **kw: object,
) -> dict[str, str]:
    """Map drafted speaker notes onto the FINAL compiled PDF's pages, splitting
    any logical slide the compile loop spread across K frames into K coherent
    per-page note segments (F3 T9).

    - ``group_logical_slides(map_pages_to_slides(final_tex))`` yields, in
      document order, the page groups: each title/structural page alone, each
      run of same-frametitle content pages together.
    - CONTENT groups map to ``drafts`` IN ORDER (Nth content group ↔ drafts[N]).
      A single-page content group gets the draft's note verbatim (no LLM). A
      K>1 group calls the note-split LLM and maps the K segments to the pages.
    - TITLE / structural pages get an empty note (never ``"(continued)"``).
    - Degrades gracefully when the group/draft counts disagree (coherence merged
      or added frames): zip by the shorter, reuse the nearest draft note for any
      unmapped content page. Always returns EXACTLY ``{str(p): note}`` for every
      page ``1..page_count``.
    """
    page_count = max(page_count, 0)
    pages = map_pages_to_slides(final_tex)
    pages_map: dict[int, str | None] = {ps.page: ps.frametitle for ps in pages}
    title_pages = {ps.page for ps in pages if ps.is_title}
    groups = group_logical_slides(pages)
    content_groups = [g for g in groups if g and g[0] not in title_pages]

    notes: dict[str, str] = {str(p): "" for p in range(1, page_count + 1)}

    # Concurrently split every multi-page content group; map single-page groups
    # (and degraded/unmapped pages) deterministically with no LLM call.
    async def _split(group: list[int], draft_note: str, title: str) -> list[str]:
        try:
            result = await adapter.structured(
                slot="slides_note_split/v1",
                variables={
                    "slide_title": title,
                    "page_count": len(group),
                    "full_note": draft_note,
                    "response_language": response_language or "the user's language",
                },
                response_model=NoteSegments,
                model=model,
            )
            return _coerce_segments(result.segments, len(group), draft_note)
        except Exception:  # noqa: BLE001 — never let a note-split failure drop the deck
            # Deterministic sentence-split fallback: real per-page speech, no LLM.
            return _coerce_segments(
                _deterministic_split(draft_note, len(group)), len(group), draft_note,
            )

    split_count = 0
    coroutines: list[Coroutine[Any, Any, list[str]]] = []
    coro_targets: list[list[int]] = []  # the page group each coroutine fills

    n = min(len(content_groups), len(drafts))
    for i in range(n):
        group = content_groups[i]
        note = drafts[i].note.strip()
        if len(group) == 1:
            notes[str(group[0])] = note
        else:
            split_count += 1
            coroutines.append(
                _split(group, note, _frametitle_for_group(pages_map, group))
            )
            coro_targets.append(group)

    # Degradation: more content groups than drafts → reuse the nearest (last)
    # draft note for the surplus pages so no content page is left blank.
    if drafts and len(content_groups) > len(drafts):
        fallback_note = drafts[-1].note.strip()
        for group in content_groups[len(drafts):]:
            for p in group:
                notes[str(p)] = fallback_note

    async with tracer.step(
        agent="report", tool="report:notes_finalize", model=model
    ) as step:
        step.record_args(
            {
                "draft_count": len(drafts),
                "page_count": page_count,
                "group_sizes": [len(g) for g in groups],
                "content_group_sizes": [len(g) for g in content_groups],
                "title_pages": sorted(title_pages),
            }
        )
        if coroutines:
            results = await asyncio.gather(*coroutines)
            for group, segments in zip(coro_targets, results, strict=True):
                for p, seg in zip(group, segments, strict=True):
                    notes[str(p)] = seg
        step.record_result(
            {
                "llm_split_count": split_count,
                "final_page_count": page_count,
                "note_pages": sorted(notes.keys()),
            }
        )
    return notes
