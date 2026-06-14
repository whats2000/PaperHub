"""F6.1-R sl_outline — digest-driven narrative planning orchestrator.

Replaces the slow full-paper "gather" loop with a digest-driven structure:
the orchestrator LLM sees a cheap cached per-section DIGEST of every paper —
FULL COVERAGE of what each section says — and structures the WHOLE deck from
it.  It requests deterministic ``read_section`` fetches (no LLM,
``read_section_chunks``) ONLY for a slide's exact evidence (a precise number,
a figure detail, an equation's terms).

Architecture:

    round 1: LLM sees the digest map + empty read-block.
             -> read(reads=[{paper_id, section_name}, ...])  OR  finalize(outline=...)
    round N: LLM sees the digest map + the targeted reads so far.
             -> finalize(outline=...)

Resolution (``DeckOutlineDraft`` -> ``DeckOutline``):
- slide_index assigned by order.
- grounding_chunk_ids = union of ``reads_by_key[key].chunk_ids`` for each
  ``key`` in slide.cites_reads (the LLM points a slide at evidence by naming
  the read key "<paper_id>:<section_name>", never by emitting raw chunk ids).
- support_excerpts = ``[<section>] text[:400]`` from each cited read
  (first 6 excerpts to avoid bloat).
- figure_key / paper_id clamped to the digest inventory; misses recorded in
  ``dropped``.

Traced as ``report:outline``; the result records the digests summary,
per-round actions, final outline, narrative_pattern, rounds_used, the
per-key read-evidence map (``reads``), and dropped.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from paperhub.agents.sl_read import ReadResult
from paperhub.agents.sl_seed import _looks_like_survey
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.slide_domain import (
    DeckOutline,
    DeckOutlineDraft,
    OutlineResult,
    OutlineSlide,
    OutlineSlideDraft,
    PaperDigest,
    ReadRequest,
    RoundAction,
    SourceSection,
)
from paperhub.tracing.tracer import Tracer

# Maximum support excerpts injected per slide to keep prompt size bounded.
_MAX_SUPPORT_EXCERPTS = 6
# Read caps — the orchestrator LLM can over-ask (empty/duplicate sections); a
# read is a cheap deterministic SQL fetch, but bound the fan-out so a runaway
# round can't pull the whole paper. The digest already gives full coverage.
_MAX_READS_PER_ROUND = 6
_MAX_TOTAL_READS = 12


# ---------------------------------------------------------------------------
# Read-key canonicalization
# ---------------------------------------------------------------------------

def _read_key(paper_id: int, section: str) -> str:
    """Canonical key for a (paper_id, section) read.

    Used SYMMETRICALLY for STORING reads and RESOLVING ``cites_reads`` — so the
    LLM's ``"73:Method"`` matches the stored read regardless of spacing/case.
    """
    return f"{int(paper_id)}:{' '.join(section.split()).lower()}"


# ---------------------------------------------------------------------------
# Prompt-formatting helpers
# ---------------------------------------------------------------------------

def _format_digest_block(digests: list[PaperDigest]) -> str:
    """Render the digest map block for the orchestrator prompt.

    This block is the FULL-COVERAGE map: every section's insight is here, so the
    LLM can structure the WHOLE deck from it and only ``read`` for exact evidence.
    """
    parts: list[str] = []
    for d in digests:
        survey_tag = (
            " [SURVEY — decompose into its surveyed branches]"
            if _looks_like_survey(d.title, d.abstract)
            else ""
        )
        section_lines = "\n".join(f"- {s.name}: {s.insight}" for s in d.sections) or "- (none)"
        figs = ", ".join(f.key for f in d.figures) or "(none)"
        block = (
            f"### paper_id={d.paper_id}: {d.title}{survey_tag}\n"
            f"Abstract: {d.abstract[:600]}\n"
            f"Section insights:\n{section_lines}\n"
            f"Figure keys: {figs}"
        )
        if d.key_equations:
            eqs = "\n".join(
                f"  - {e.latex}" + (f"  ({e.role})" if e.role else "")
                for e in d.key_equations
            )
            block += f"\nKey equations:\n{eqs}"
        parts.append(block)
    return "\n\n".join(parts)


def _format_read_block(reads_by_key: dict[str, ReadResult]) -> str:
    """Render the accumulated targeted-reads block for the orchestrator prompt."""
    if not reads_by_key:
        return "(no targeted reads yet — request read_section for a slide's exact evidence)"
    parts: list[str] = []
    for key, res in reads_by_key.items():
        parts.append(f"[{key}]\n  chunk_ids={res.chunk_ids}\n  {res.text[:500]}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _resolve_slide(
    idx: int,
    s: OutlineSlideDraft,
    *,
    reads_by_key: dict[str, ReadResult],
    known_paper_ids: set[int],
    known_fig_keys: set[str],
    dropped: list[str],
) -> OutlineSlide:
    """Resolve one draft slide deterministically."""
    # --- paper_id clamp ---
    paper_id: int | None = s.paper_id
    if paper_id is not None and paper_id not in known_paper_ids:
        dropped.append(f"slide{idx}:paper_id={paper_id}:not-in-digests")
        paper_id = None

    # --- figure_key clamp ---
    figure_key: str | None = s.figure_key
    if figure_key is not None and figure_key not in known_fig_keys:
        dropped.append(f"slide{idx}:figure_key={figure_key!r}:not-in-digests")
        figure_key = None

    # --- grounding from cites_reads ---
    chunk_ids: list[int] = []
    support_excerpts: list[str] = []
    source_sections: list[SourceSection] = []
    for raw in s.cites_reads:
        pid_str, sep, section = raw.partition(":")
        section = section.strip()
        if not sep or not section:
            dropped.append(f"slide{idx}:cites_read={raw!r}:malformed")
            continue
        try:
            pid = int(pid_str.strip())
        except ValueError:
            dropped.append(f"slide{idx}:cites_read={raw!r}:malformed")
            continue
        key = _read_key(pid, section)
        res = reads_by_key.get(key)
        if res is None:
            dropped.append(f"slide{idx}:cites_read={key!r}:not-read")
            continue
        chunk_ids.extend(res.chunk_ids)
        support_excerpts.append(f"[{section}] {res.text[:400]}")
        # Per-section grounding for the deck_slides traceability north star —
        # one entry per successfully-resolved cited section (no cross-section
        # dedup; chunk_ids stay as read).
        source_sections.append(
            SourceSection(paper_id=pid, section_name=section, chunk_ids=list(res.chunk_ids))
        )

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
        source_sections=source_sections,
    )


def _ensure_front_title(
    slides: list[OutlineSlide], talk_title: str, dropped: list[str]
) -> list[OutlineSlide]:
    """Guarantee the deck opens with a single front title slide.

    The base writer renders 1:1 with the outline (it does NOT add slides), so an
    outline whose first slide isn't a ``title`` produces a deck with NO
    ``\\titlepage`` at all — the live run 569 regression. This deterministic
    guard restores the front title the old all-in-one agent always emitted:
    prepend a title slide when the first slide isn't one, and demote any
    LATER ``title`` slide to a ``section_divider`` (an interval title page is a
    layout smell the user explicitly rejects — single-paper decks want the front
    title ONLY). Slides are renumbered 0..N-1 to keep the 1:1 deck_slides
    contract.
    """
    if not slides or slides[0].content_form != "title":
        slides = [
            OutlineSlide(
                slide_index=0,
                goal="Title slide",
                key_message=talk_title,
                content_form="title",
                transition_from_prev="",
                speaker_note_hint="",
                paper_id=None,
                figure_key=None,
                grounding_chunk_ids=[],
                support_excerpts=[],
                source_sections=[],
            ),
            *slides,
        ]
    # Demote any stray non-front title slide (interval title page).
    fixed: list[OutlineSlide] = []
    for i, s in enumerate(slides):
        if i > 0 and s.content_form == "title":
            dropped.append(f"slide{i}:interval-title->section_divider")
            s = s.model_copy(update={"content_form": "section_divider"})
        fixed.append(s.model_copy(update={"slide_index": i}))
    return fixed


def _resolve_outline(
    draft: DeckOutlineDraft,
    *,
    reads_by_key: dict[str, ReadResult],
    known_paper_ids: set[int],
    known_fig_keys: set[str],
    narrative_pattern: str,
) -> tuple[DeckOutline, list[str]]:
    """Resolve a DeckOutlineDraft -> DeckOutline + dropped list."""
    dropped: list[str] = []
    resolved_slides = [
        _resolve_slide(
            idx, s,
            reads_by_key=reads_by_key,
            known_paper_ids=known_paper_ids,
            known_fig_keys=known_fig_keys,
            dropped=dropped,
        )
        for idx, s in enumerate(draft.slides)
    ]
    resolved_slides = _ensure_front_title(resolved_slides, draft.talk_title, dropped)
    outline = DeckOutline(
        talk_title=draft.talk_title,
        narrative_pattern=narrative_pattern,
        audience_intent=draft.audience_intent,
        narrative_arc=draft.narrative_arc,
        slides=resolved_slides,
    )
    return outline, dropped


def _minimal_outline(digests: list[PaperDigest], task_description: str) -> DeckOutlineDraft:
    """Synthesize a minimal outline from digest data when the LLM never finalizes."""
    slides: list[OutlineSlideDraft] = [
        OutlineSlideDraft(goal="Title", key_message=task_description, content_form="title"),
    ]
    for d in digests:
        slides.append(
            OutlineSlideDraft(
                goal=f"Overview of {d.title}",
                key_message=d.abstract[:200],
                content_form="bullets",
                paper_id=d.paper_id,
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
    digests: list[PaperDigest],
    task_description: str,
    response_language: str,
    target_slides: int,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    read_fn: Callable[[int, str], Awaitable[ReadResult]],
    max_rounds: int = 4,
) -> OutlineResult:
    """Run the digest-driven narrative planning loop.

    Args:
        digests: cheap cached per-section digests — FULL COVERAGE of what every
            section says.  The orchestrator structures the whole deck from these.
        task_description: the user's slide request.
        response_language: language for all human-readable text in the outline.
        target_slides: target number of content slides (from parse_slide_budget).
        adapter: LLM adapter (structured-output interface).
        tracer: open Tracer bound to the current run.
        model: litellm model id.
        read_fn: ``(paper_id, section_name) -> ReadResult``; injected so tests can
            stub it.  The caller (report_graph) passes a closure binding conn.
        max_rounds: maximum read rounds before forcing finalize.

    Returns:
        OutlineResult with the resolved DeckOutline and how many rounds were used.
    """
    known_paper_ids = {d.paper_id for d in digests}
    known_fig_keys: set[str] = {f.key for d in digests for f in d.figures}

    reads_by_key: dict[str, ReadResult] = {}
    read_keys: set[str] = set()  # canonical keys already fetched (dedup guard)
    round_log: list[dict[str, Any]] = []
    narrative_pattern = "synthesis"  # default; overridden by first LLM response
    final_draft: DeckOutlineDraft | None = None
    rounds_used = 0

    async with tracer.step(agent="report", tool="report:outline", model=model) as step:
        step.record_args(
            {
                "digests": [
                    {
                        "paper_id": d.paper_id,
                        "title": d.title,
                        "n_sections": len(d.sections),
                        "n_figures": len(d.figures),
                    }
                    for d in digests
                ],
                "task_description": task_description,
                "target_slides": target_slides,
                "max_rounds": max_rounds,
            }
        )

        digest_block = _format_digest_block(digests)  # constant per run

        for round_num in range(1, max_rounds + 1):
            rounds_used = round_num
            is_last_round = round_num == max_rounds
            read_block = _format_read_block(reads_by_key)

            action: RoundAction = await adapter.structured(
                slot="slides_outline/v1",
                variables={
                    "task_description": task_description,
                    "response_language": response_language,
                    "target_slides": target_slides,
                    "digest_block": digest_block,
                    "read_block": read_block,
                    "round_number": round_num,
                    "max_rounds": max_rounds,
                    "must_finalize": (
                        "YES — this is the LAST round; you MUST emit action=finalize now."
                        if is_last_round
                        else "no"
                    ),
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

            if action.action == "read" and not is_last_round:
                # Filter the LLM's reads BEFORE fetching: an empty section, a
                # paper not in the digests, a within-round duplicate, or a section
                # already read each gets dropped. Cap per-round and total so a
                # runaway read can't pull the whole paper (digest = full coverage).
                seen_round: set[str] = set()
                fresh: list[ReadRequest] = []
                for r in action.reads:
                    section = r.section_name.strip()
                    if not section or r.paper_id not in known_paper_ids:
                        continue
                    key = _read_key(r.paper_id, section)
                    if key in read_keys or key in seen_round:
                        continue
                    seen_round.add(key)
                    fresh.append(r)
                    if (
                        len(fresh) >= _MAX_READS_PER_ROUND
                        or len(reads_by_key) + len(fresh) >= _MAX_TOTAL_READS
                    ):
                        break
                round_entry["requested_reads"] = [
                    {"paper_id": r.paper_id, "section_name": r.section_name} for r in fresh
                ]
                round_entry["skipped"] = len(action.reads) - len(fresh)
                round_entry["fetched_keys"] = [
                    _read_key(r.paper_id, r.section_name) for r in fresh
                ]
                round_log.append(round_entry)

                if not fresh:
                    # Nothing NEW to fetch — no more evidence to gather.
                    continue

                results: list[ReadResult] = await asyncio.gather(
                    *[read_fn(r.paper_id, r.section_name) for r in fresh]
                )
                for r, res in zip(fresh, results, strict=True):
                    key = _read_key(r.paper_id, r.section_name)
                    reads_by_key[key] = res
                    read_keys.add(key)
                continue

            # Either: action==read on the last round, action==finalize with no
            # outline, or any other unexpected state. Treat as "finalize with
            # whatever outline was returned (if any), else synthesize a minimal one".
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
            final_draft = _minimal_outline(digests, task_description)
            narrative_pattern = "synthesis"

        outline, dropped = _resolve_outline(
            final_draft,
            reads_by_key=reads_by_key,
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
                # Per-slide traceback evidence a future Sources UI needs.
                "reads": {key: res.chunk_ids for key, res in reads_by_key.items()},
                "dropped": dropped,
            }
        )

    return OutlineResult(outline=outline, rounds_used=rounds_used)
