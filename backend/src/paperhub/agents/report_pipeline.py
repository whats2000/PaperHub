"""Traced LLM-calling units for the Report Agent subgraph (Plan F4/F4.5).

The F4 follow-up units (classify_deck_command, edit_frame, revise_tex,
edit_title_block, edit_preamble_block, author_deck_notes) are each wrapped in
a Tracer step per the agent-flow observability policy (CLAUDE.md). Every step
records enough state to reconstruct the agent context entirely from the DB
alone. Speaker notes are authored separately by ``author_deck_notes`` — a
deck-wide single-call author that sees ALL frames + the source-paper context
so notes have a real narrative arc (foreshadow / callback) and stay grounded
in the actual research, not generic prose. The earlier per-slide
``author_note`` path was retired with that change (see SRS v2.25 / F4.5).
The F3/F4 R1 fan-out helpers (understand_paper, narrate_talk, draft_frame,
coherence_pass) were removed in the F4.5 monolithic-slide-agent cleanup —
the slide_agent + gather_context paths replaced them.
"""
from __future__ import annotations

import re

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import (
    DeckCommand,
    DeckNotesAuthor,
    TargetLanguage,
)
from paperhub.tracing.tracer import Tracer

# Strip a leading/trailing markdown code fence (```latex ... ```), tolerating an
# optional language tag on the opening fence.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")

# NOTE: deck-LENGTH parsing used to live here (a CJK-only regex
# ``parse_slide_budget``). It was removed: it could not honor non-CJK units
# ("pages"), ranges, or durations, and silently returned a wrong default that
# CONTRADICTED the user's task (e.g. "20-30 pages" → 15). The outline now reads
# the requested length straight from the task (any language), defaulting to ~15
# only when none is named.


# --------------------------------------------------------------------------
# F4 / F4.5 helpers (SRS v2.21+).
#
# The F3/F4 R1 fan-out helpers (understand_paper, narrate_talk, draft_frame,
# coherence_pass) were removed in the F4.5 cleanup — superseded by the
# monolithic slide_agent + gather_context path.
# --------------------------------------------------------------------------
def _strip_code_fences(text: str) -> str:
    """Remove a wrapping markdown code fence from an LLM stream, if present."""
    out = text.strip()
    if out.startswith("```"):
        out = _FENCE_RE.sub("", out)
        out = _FENCE_RE.sub("", out)
    return out.strip()


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
        revised = _strip_code_fences("".join(tokens))
        if not revised:
            revised = tex
        step.record_result({"log_len": len(pdflatex_log), "changed": revised != tex})
    return revised


# --------------------------------------------------------------------------
# F4: DeckCommand classifier (SRS v2.21).
# --------------------------------------------------------------------------

async def classify_deck_command(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, instruction: str,
    current_view_page: int, deck_outline: str, slide_attached: bool = False,
) -> DeckCommand:
    """Classify a slides follow-up turn (when a deck already exists) into one
    :class:`DeckCommand` action.  Slot ``slides_deck_command/v1``; traced as
    ``report:deck_command``."""
    async with tracer.step(agent="report", tool="report:deck_command", model=model) as step:
        step.record_args({
            "instruction": instruction,
            "current_view_page": current_view_page,
            "slide_attached": slide_attached,
        })
        dec = await adapter.structured(
            slot="slides_deck_command/v1",
            variables={
                "instruction": instruction,
                "current_view_page": current_view_page,
                "deck_outline": deck_outline,
                "slide_attached": slide_attached,
            },
            response_model=DeckCommand,
            model=model,
        )
        step.record_result(dec.model_dump())
    return dec


async def detect_slide_language(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, instruction: str,
) -> str | None:
    """Detect the language the user EXPLICITLY asked the SLIDE CONTENT to be in
    (e.g. "把簡報換成英文" → "English"), independent of the chat-reply language.
    Returns the language name, or ``None`` when none was named (caller falls
    back to ``response_language``). Slot ``slides_target_language/v1``; traced as
    ``report:detect_language``."""
    async with tracer.step(
        agent="report", tool="report:detect_language", model=model
    ) as step:
        step.record_args({"instruction": instruction})
        out = await adapter.structured(
            slot="slides_target_language/v1",
            variables={"instruction": instruction},
            response_model=TargetLanguage,
            model=model,
        )
        step.record_result(out.model_dump())
    return out.language


# --------------------------------------------------------------------------
# F4: Note-author + frame-edit streaming functions (SRS v2.21, Task 8).
# --------------------------------------------------------------------------

async def author_deck_notes(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    papers: list[dict[str, object]],
    frames: list[tuple[int, int, str]],  # (slide_index, page_start, frame_tex)
    existing_notes: dict[int, str],  # slide_index -> existing note (kept verbatim)
    wanted_indices: list[int],  # which slides to author/re-author
    note_language: str,
    instruction: str | None = None,
) -> dict[int, str]:
    """Author speaker notes for the WANTED subset of slides in ONE pass that
    sees the entire deck + the source-paper context.

    Why one pass: a talk has narrative arc — slide N's note may foreshadow
    slide N+3, slide N may call back to slide N-2, the closing slide needs
    to know what the opening promised. Per-slide authoring can't do this.

    The model:
      - reads PAPERS (title / authors / abstract per contributing paper) so
        each note stays grounded in the actual research, not generic prose;
      - reads every frame (so cross-slide references are real);
      - reads EXISTING notes for slides we're NOT regenerating, so a single
        edit's re-authored note matches the talk's voice + level of detail;
      - writes notes ONLY for ``wanted_indices`` (one per index, in order).

    Returns ``{slide_index: note_text}`` for the wanted indices. Structured
    output via ``DeckNotesAuthor``; the schema forces ``extra=forbid`` so the
    model's drift to extra fields trips a retry. Slot
    ``slides_deck_notes_author/v1``; traced as ``report:deck_notes_author``.
    """
    # Render the papers block. Prefer the rich PaperContextBundle fields
    # (narrative_summary + key_figures + key_equations + section_excerpts)
    # that the slide_agent itself consumed at generate time — the speaker
    # needs the SAME grounding the slides were built from, not just the bare
    # abstract. Fall back to abstract when those fields are absent (legacy
    # decks pre-dating bundle persistence, or single-call notes turns).
    def _authors(p: dict[str, object]) -> str:
        a = p.get("authors")
        if isinstance(a, list):
            return ", ".join(str(x) for x in a if x)
        return str(a) if a else ""

    def _render(p: dict[str, object], idx: int) -> str:
        lines: list[str] = [
            f"[paper {idx + 1}] {p.get('title') or '(untitled)'}",
            f"  authors: {_authors(p) or '(unknown)'}",
        ]
        narrative = p.get("narrative_summary")
        if narrative:
            lines.append(f"  narrative: {narrative}")
        else:
            abstract = p.get("abstract")
            lines.append(f"  abstract: {abstract or '(no abstract)'}")
        key_figures = p.get("key_figures") or []
        if isinstance(key_figures, list) and key_figures:
            lines.append("  key figures:")
            for f in key_figures:
                if isinstance(f, dict):
                    key = f.get("key") or "(no key)"
                    role = f.get("role") or "?"
                    interp = (
                        f.get("one_line_interpretation")
                        or f.get("interpretation")
                        or ""
                    )
                    lines.append(f"    - {key} ({role}): {interp}")
        key_eqs = p.get("key_equations") or []
        if isinstance(key_eqs, list) and key_eqs:
            lines.append("  key equations (verbalize, NEVER quote LaTeX in spoken text):")
            for eq in key_eqs:
                if isinstance(eq, dict):
                    role = eq.get("role") or "?"
                    legend = eq.get("notation_legend") or ""
                    lines.append(f"    - {role}: {legend}")
        sections = p.get("section_excerpts") or []
        if isinstance(sections, list) and sections:
            lines.append("  section excerpts:")
            for s in sections:
                if isinstance(s, dict):
                    name = s.get("section_name") or "(unnamed)"
                    text = (s.get("text") or "").strip()
                    if text:
                        # Cap at ~400 chars to keep the prompt focused — the
                        # full text is still in the persisted bundle if a
                        # later turn needs to recover it.
                        snippet = text if len(text) <= 400 else text[:400] + "…"
                        lines.append(f"    - [{name}] {snippet}")
        return "\n".join(lines)

    papers_block = "\n\n".join(
        _render(p, i) for i, p in enumerate(papers)
    ) or "(no papers attached)"

    frames_block = "\n\n".join(
        f"[slide_index={idx}, page={page}]\n{tex}"
        for idx, page, tex in frames
    ) or "(no frames)"

    existing_notes_block = "\n\n".join(
        f"[slide_index={idx}]\n{note}"
        for idx, note in sorted(existing_notes.items())
    ) or "(no existing notes — all slides being authored fresh)"

    wanted_sorted = sorted(set(wanted_indices))
    wanted_block = ", ".join(str(i) for i in wanted_sorted) or "(none)"

    # Per-paper context shape so a trace inspector can verify the
    # papers_block isn't silently degraded (e.g. context_bundles.json missing
    # → fell through to abstract-only). The first chars of each rendered
    # block let you see what the model actually saw, without recording the
    # full prompt (which is template-derived and large).
    def _len_if_list(v: object) -> int:
        return len(v) if isinstance(v, list) else 0

    paper_shapes: list[dict[str, object]] = [
        {
            "title": str(p.get("title") or "")[:80],
            "has_narrative": bool(p.get("narrative_summary")),
            "has_abstract": bool(p.get("abstract")),
            "n_key_figures": _len_if_list(p.get("key_figures")),
            "n_key_equations": _len_if_list(p.get("key_equations")),
            "n_section_excerpts": _len_if_list(p.get("section_excerpts")),
        }
        for p in papers
    ]

    async with tracer.step(
        agent="report", tool="report:deck_notes_author", model=model
    ) as step:
        step.record_args(
            {
                "n_papers": len(papers),
                "paper_shapes": paper_shapes,
                "n_frames": len(frames),
                "frame_indices": [idx for idx, _, _ in frames],
                "n_existing_notes": len(existing_notes),
                "existing_note_indices": sorted(existing_notes.keys()),
                "wanted_indices": wanted_sorted,
                "note_language": note_language,
                "instruction": instruction or "(none)",
                "papers_block_head": papers_block[:600],
                "frames_block_head": frames_block[:400],
                "existing_notes_block_head": existing_notes_block[:400],
            }
        )
        out = await adapter.structured(
            slot="slides_deck_notes_author/v1",
            variables={
                "papers_block": papers_block,
                "frames_block": frames_block,
                "existing_notes_block": existing_notes_block,
                "wanted_block": wanted_block,
                "instruction": instruction or "(none)",
                "note_language": note_language or "the user's language",
            },
            response_model=DeckNotesAuthor,
            model=model,
        )
        result: dict[int, str] = {
            entry.slide_index: entry.note.strip()
            for entry in out.notes
            if entry.note.strip()
        }
        step.record_result(
            {
                "returned_indices": sorted(result.keys()),
                "n_returned": len(result),
                "missing_indices": [i for i in wanted_sorted if i not in result],
                # The actual notes (per slide) so a trace inspector can read
                # what the model produced without re-running. Capped at 800
                # chars per note (notes are 3-6 sentences, well under 800).
                "notes": {
                    str(idx): (text if len(text) <= 800 else text[:800] + "…")
                    for idx, text in sorted(result.items())
                },
            }
        )
    return result




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
        out = _strip_code_fences("".join(toks))
        step.record_result({"new_frame": out})
    return out or frame_tex


# --------------------------------------------------------------------------
# F4.2: Preamble/title-block editing functions (SRS v2.21, Task B5).
# --------------------------------------------------------------------------

async def _edit_page_block(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    slot: str,
    tool: str,
    page_block: str,
    instruction: str,
    response_language: str,
) -> str:
    """Shared implementation for :func:`edit_title_block` and
    :func:`edit_preamble_block`.  Streams an LLM rewrite of the deck's
    page-1 source block (preamble + title frame), strips code fences, and
    traces the step."""
    async with tracer.step(agent="report", tool=tool, model=model) as step:
        step.record_args({"instruction": instruction, "block_len": len(page_block)})
        toks: list[str] = []
        async for t in adapter.stream(
            slot=slot,
            variables={
                "page_block": page_block,
                "instruction": instruction,
                "response_language": response_language or "the user's language",
            },
            model=model,
        ):
            toks.append(t)
        out = _strip_code_fences("".join(toks)).strip()
        result = out or page_block  # fall back to the original on empty output
        step.record_result({"new_block_len": len(result)})
    return result


async def edit_title_block(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    page_block: str,
    instruction: str,
    response_language: str,
) -> str:
    """Rewrite the title page's metadata + title-frame layout (F4.2).

    Slot ``slides_edit_title/v1``; traced as ``report:edit_title``.
    """
    return await _edit_page_block(
        adapter=adapter,
        tracer=tracer,
        model=model,
        slot="slides_edit_title/v1",
        tool="report:edit_title",
        page_block=page_block,
        instruction=instruction,
        response_language=response_language,
    )


async def edit_preamble_block(
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    page_block: str,
    instruction: str,
    response_language: str,
) -> str:
    """Restyle the whole deck via its preamble (theme/colors/fonts/header-footer)
    (F4.2).

    Slot ``slides_edit_preamble/v1``; traced as ``report:edit_preamble``.
    """
    return await _edit_page_block(
        adapter=adapter,
        tracer=tracer,
        model=model,
        slot="slides_edit_preamble/v1",
        tool="report:edit_preamble",
        page_block=page_block,
        instruction=instruction,
        response_language=response_language,
    )
