"""F6.1 sl_outline — multi-round narrative planning orchestrator.

Replaces the one-shot F6.1 draft with an iterative aimed-gather loop:
the orchestrator LLM decides each round whether to dispatch targeted
``gather_fn(aim, paper_id)`` calls (to fetch specific evidence) or to
finalize the deck outline.  Up to ``max_rounds`` dispatch rounds are
allowed; on the final round the LLM is forced to finalize.

Architecture:

    round 1: LLM sees seed map + empty gathered-context.
             -> dispatch(requests=[{aim, paper_id}, ...])  OR  finalize(outline=...)
    round N: LLM sees seed map + all gathered bundles so far.
             -> finalize(outline=...)

Resolution (``DeckOutlineDraft`` -> ``DeckOutline``):
- slide_index assigned by order.
- grounding_chunk_ids = union of ``gathered[a].read_chunk_ids`` for each
  ``a`` in slide.cites_aims (the LLM points a slide at evidence by naming
  the aim, never by emitting raw chunk integers).
- support_excerpts = narrative_summary + section_excerpt.text from each
  cited bundle (first 6 excerpts to avoid bloat).
- figure_key / paper_id clamped to seed inventory; misses recorded in
  ``dropped``.

Traced as ``report:outline``; the result records seeds, per-round actions,
final outline, narrative_pattern, rounds_used, dropped.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.slide_domain import (
    ContextRequest,
    DeckOutline,
    DeckOutlineDraft,
    OutlineResult,
    OutlineSlide,
    OutlineSlideDraft,
    PaperContextBundle,
    RoundAction,
    SeedPaper,
)
from paperhub.tracing.tracer import Tracer

# Maximum support excerpts injected per slide to keep prompt size bounded.
_MAX_SUPPORT_EXCERPTS = 6
# Dispatch caps — the orchestrator LLM can over-ask (empty/duplicate aims); each
# aim is a full, slow flagship gather, so bound how many actually run.
_MAX_AIMS_PER_ROUND = 4
_MAX_TOTAL_AIMS = 8


# ---------------------------------------------------------------------------
# Prompt-formatting helpers
# ---------------------------------------------------------------------------

def _format_seed_map(seeds: list[SeedPaper]) -> str:
    """Render the seed map block for the orchestrator prompt."""
    parts: list[str] = []
    for s in seeds:
        survey_tag = " [SURVEY — decompose into its surveyed branches]" if s.is_survey else ""
        figs = ", ".join(f.key for f in s.figures) or "(none)"
        secs = ", ".join(s.sections) or "(none)"
        parts.append(
            f"### paper_id={s.paper_id}: {s.title}{survey_tag}\n"
            f"Abstract: {s.abstract[:600]}\n"
            f"Sections (dispatch targets): {secs}\n"
            f"Figure keys: {figs}"
        )
    return "\n\n".join(parts)


def _format_gathered_block(gathered: dict[str, PaperContextBundle]) -> str:
    """Render the accumulated gathered-context block for the orchestrator prompt."""
    if not gathered:
        return "(no targeted evidence gathered yet — dispatch aims to fetch specific detail)"
    parts: list[str] = []
    for aim, bundle in gathered.items():
        excerpts = "\n".join(
            f"  [{e.section_name}] {e.text[:400]}" for e in bundle.section_excerpts[:4]
        )
        parts.append(
            f"[aim={aim!r}  paper_id={bundle.paper_id}]\n"
            f"Narrative summary: {bundle.narrative_summary[:600]}\n"
            f"Section excerpts:\n{excerpts or '  (none)'}"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _resolve_slide(
    idx: int,
    s: OutlineSlideDraft,
    *,
    gathered: dict[str, PaperContextBundle],
    known_paper_ids: set[int],
    known_fig_keys: set[str],
    dropped: list[str],
) -> OutlineSlide:
    """Resolve one draft slide deterministically."""
    # --- paper_id clamp ---
    paper_id: int | None = s.paper_id
    if paper_id is not None and paper_id not in known_paper_ids:
        dropped.append(f"slide{idx}:paper_id={paper_id}:not-in-seeds")
        paper_id = None

    # --- figure_key clamp ---
    figure_key: str | None = s.figure_key
    if figure_key is not None and figure_key not in known_fig_keys:
        dropped.append(f"slide{idx}:figure_key={figure_key!r}:not-in-seeds")
        figure_key = None

    # --- grounding from cites_aims ---
    chunk_ids: list[int] = []
    support_excerpts: list[str] = []
    for aim in s.cites_aims:
        bundle = gathered.get(aim)
        if bundle is None:
            dropped.append(f"slide{idx}:cites_aim={aim!r}:not-gathered")
            continue
        chunk_ids.extend(bundle.read_chunk_ids)
        # narrative_summary as first excerpt entry
        if bundle.narrative_summary:
            support_excerpts.append(bundle.narrative_summary[:600])
        for e in bundle.section_excerpts:
            support_excerpts.append(f"[{e.section_name}] {e.text[:400]}")

    # Cap to avoid prompt bloat in downstream drafter
    support_excerpts = support_excerpts[:_MAX_SUPPORT_EXCERPTS]

    return OutlineSlide(
        slide_index=idx,
        goal=s.goal,
        key_message=s.key_message,
        content_form=s.content_form,
        transition_from_prev=s.transition_from_prev,
        speaker_note_hint=s.speaker_note_hint,
        paper_id=paper_id,
        figure_key=figure_key,
        grounding_chunk_ids=sorted(set(chunk_ids)),
        support_excerpts=support_excerpts,
    )


def _resolve_outline(
    draft: DeckOutlineDraft,
    *,
    gathered: dict[str, PaperContextBundle],
    known_paper_ids: set[int],
    known_fig_keys: set[str],
    narrative_pattern: str,
) -> tuple[DeckOutline, list[str]]:
    """Resolve a DeckOutlineDraft -> DeckOutline + dropped list."""
    dropped: list[str] = []
    resolved_slides = [
        _resolve_slide(
            idx, s,
            gathered=gathered,
            known_paper_ids=known_paper_ids,
            known_fig_keys=known_fig_keys,
            dropped=dropped,
        )
        for idx, s in enumerate(draft.slides)
    ]
    outline = DeckOutline(
        talk_title=draft.talk_title,
        narrative_pattern=narrative_pattern,
        audience_intent=draft.audience_intent,
        narrative_arc=draft.narrative_arc,
        slides=resolved_slides,
    )
    return outline, dropped


def _minimal_outline(seeds: list[SeedPaper], task_description: str) -> DeckOutlineDraft:
    """Synthesize a minimal outline from seed data when the LLM never finalizes."""
    slides: list[OutlineSlideDraft] = [
        OutlineSlideDraft(goal="Title", key_message=task_description, content_form="title"),
    ]
    for s in seeds:
        slides.append(
            OutlineSlideDraft(
                goal=f"Overview of {s.title}",
                key_message=s.abstract[:200],
                content_form="bullets",
                paper_id=s.paper_id,
            )
        )
    slides.append(
        OutlineSlideDraft(goal="Summary", key_message="Key takeaways", content_form="synthesis")
    )
    return DeckOutlineDraft(
        talk_title=task_description,
        narrative_pattern="synthesis",
        audience_intent="walk through the cited references",
        narrative_arc="intro -> papers -> synthesis",
        slides=slides,
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_sl_outline(
    *,
    seeds: list[SeedPaper],
    task_description: str,
    response_language: str,
    target_slides: int,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    gather_fn: Callable[[str, int], Awaitable[PaperContextBundle]],
    max_rounds: int = 4,
) -> OutlineResult:
    """Run the multi-round narrative planning loop.

    Args:
        seeds: deterministic high-level map of the deck's papers (the
            dispatch menu the orchestrator may aim at).
        task_description: the user's slide request.
        response_language: language for all human-readable text in the outline.
        target_slides: target number of content slides (from parse_slide_budget).
        adapter: LLM adapter (structured-output interface).
        tracer: open Tracer bound to the current run.
        model: litellm model id.
        gather_fn: ``(aim, paper_id) -> PaperContextBundle``; injected so
            tests can stub it without needing a real DB + LLM.  The caller
            (report_graph) passes a closure that binds adapter/conn/etc.
        max_rounds: maximum dispatch rounds before forcing finalize.

    Returns:
        OutlineResult with the resolved DeckOutline and how many rounds were used.
    """
    known_paper_ids = {s.paper_id for s in seeds}
    known_fig_keys: set[str] = {f.key for s in seeds for f in s.figures}

    gathered: dict[str, PaperContextBundle] = {}
    gathered_keys: set[str] = set()  # normalized aims already fetched (dedup guard)
    round_log: list[dict[str, Any]] = []
    narrative_pattern = "synthesis"  # default; overridden by first LLM response
    final_draft: DeckOutlineDraft | None = None
    rounds_used = 0

    async with tracer.step(agent="report", tool="report:outline", model=model) as step:
        step.record_args(
            {
                "seeds": [
                    {
                        "paper_id": s.paper_id,
                        "title": s.title,
                        "is_survey": s.is_survey,
                        "n_sections": len(s.sections),
                        "n_figures": len(s.figures),
                    }
                    for s in seeds
                ],
                "task_description": task_description,
                "target_slides": target_slides,
                "max_rounds": max_rounds,
            }
        )

        seed_map_block = _format_seed_map(seeds)

        for round_num in range(1, max_rounds + 1):
            rounds_used = round_num
            is_last_round = round_num == max_rounds
            gathered_block = _format_gathered_block(gathered)

            action: RoundAction = await adapter.structured(
                slot="slides_outline/v1",
                variables={
                    "task_description": task_description,
                    "response_language": response_language,
                    "target_slides": target_slides,
                    "seed_map_block": seed_map_block,
                    "gathered_block": gathered_block,
                    "round_number": round_num,
                    "max_rounds": max_rounds,
                    "must_finalize": "YES — this is the LAST round; you MUST emit action=finalize now." if is_last_round else "no",
                },
                response_model=RoundAction,
                model=model,
            )

            # Capture narrative_pattern from the first response that sets it
            if action.narrative_pattern and action.narrative_pattern != "synthesis":
                narrative_pattern = action.narrative_pattern
            elif round_num == 1:
                narrative_pattern = action.narrative_pattern  # accept even default

            round_entry: dict[str, Any] = {
                "round": round_num,
                "action": action.action,
                "narrative_pattern": action.narrative_pattern,
            }

            if action.action == "finalize" and action.outline is not None:
                round_entry["n_slides"] = len(action.outline.slides)
                round_log.append(round_entry)
                final_draft = action.outline
                break

            if action.action == "dispatch" and action.requests and not is_last_round:
                # Filter the LLM's requests BEFORE gathering: an empty aim, a
                # duplicate within the round, or an aim already gathered each
                # triggers a full (slow, expensive) flagship gather otherwise —
                # this is the cause of the 8x-redundant-gather token waste. Cap
                # per-round and total so a runaway dispatch can't fan out.
                seen_round: set[str] = set()
                fresh: list[ContextRequest] = []
                for r in action.requests:
                    key = " ".join(r.aim.split()).lower()  # normalize whitespace+case
                    if not key or key in gathered_keys or key in seen_round:
                        continue
                    seen_round.add(key)
                    fresh.append(r)
                    if (
                        len(fresh) >= _MAX_AIMS_PER_ROUND
                        or len(gathered) + len(fresh) >= _MAX_TOTAL_AIMS
                    ):
                        break
                round_entry["dispatched_aims"] = [
                    {"aim": r.aim, "paper_id": r.paper_id} for r in fresh
                ]
                round_entry["skipped_requests"] = len(action.requests) - len(fresh)
                round_log.append(round_entry)

                if not fresh:
                    # Nothing NEW to fetch (all empty/duplicate/already-have) —
                    # there is no more evidence to gather, so move toward finalize.
                    continue

                bundles: list[PaperContextBundle] = await asyncio.gather(
                    *[gather_fn(r.aim, r.paper_id) for r in fresh]
                )
                for req, bundle in zip(fresh, bundles, strict=True):
                    gathered[req.aim] = bundle
                    gathered_keys.add(" ".join(req.aim.split()).lower())
                continue

            # Either: action==dispatch on the last round, or action==finalize with
            # no outline, or any other unexpected state.  Treat as "finalize with
            # whatever outline was returned (if any) or synthesize a minimal one."
            if action.outline is not None:
                round_entry["n_slides"] = len(action.outline.slides)
                round_log.append(round_entry)
                final_draft = action.outline
            else:
                round_entry["forced_fallback"] = True
                round_log.append(round_entry)
            break

        # If we exhausted rounds without a finalize, synthesize a minimal outline
        if final_draft is None:
            final_draft = _minimal_outline(seeds, task_description)
            narrative_pattern = "synthesis"

        outline, dropped = _resolve_outline(
            final_draft,
            gathered=gathered,
            known_paper_ids=known_paper_ids,
            known_fig_keys=known_fig_keys,
            narrative_pattern=narrative_pattern,
        )

        step.record_result(
            {
                "talk_title": outline.talk_title,
                "narrative_pattern": outline.narrative_pattern,
                "audience_intent": outline.audience_intent,
                "narrative_arc": outline.narrative_arc,
                "n_slides": len(outline.slides),
                "slides": [s.model_dump() for s in outline.slides],
                "rounds_used": rounds_used,
                "round_log": round_log,
                "n_aims_gathered": len(gathered),
                "aims_gathered": list(gathered.keys()),
                "dropped": dropped,
            }
        )

    return OutlineResult(outline=outline, rounds_used=rounds_used)
