"""Cross-paper deck planner for the F4.4 slide pipeline (T2).

Consumes N :class:`PaperTalkBrief` inputs (produced by T1's
:func:`paperhub.agents.sl_paper_brief.run_sl_paper_brief`) and emits a
:class:`DeckOutline` naming the slide patterns the renderer (T3) will
materialise. SINGLE LLM call (no tool loop).

Hard contracts (closes T3's renderer burden):

- The LLM's response is parsed via ``DeckOutline.model_validate_json``;
  on failure the tracer step is flipped to ``status='error'`` with the
  canonical marker ``"plan_parse_failed"`` and the ``ValidationError``
  re-raised. Per the agent-flow observability iron rule, a silent
  fallback that emits structurally-valid garbage downstream is exactly
  the failure mode we refuse to swallow.
- After parsing, every PlannedSlide is validated against the input
  briefs: ``paper_id`` must match one of the inputs;  ``figure_key``
  must match a key from that paper's ``key_figures``;
  ``equation_index`` must be a valid index into that paper's
  ``key_equations``. Any mismatch raises ``ValueError`` (caught by the
  tracer's ``except``-block, status flipped to ``'error'``, propagated up).

Subgraph wiring lands in T5; this module ships the node + tests only.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import litellm
from pydantic import ValidationError

from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import DeckOutline, PaperTalkBrief
from paperhub.tracing.tracer import Tracer

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    from paperhub.agents.report_graph import ReportDeps

__all__ = ["run_sl_plan_deck"]


# Strip a wrapping markdown code fence (```json ... ```) so a fenced JSON
# response still validates. Tolerates an optional language tag.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")

# Patterns that are inherently cross-paper — their ``paper_id`` MUST be
# null per the prompt contract. The validation pass enforces this so a
# rogue planner can't smuggle a per-paper attribution onto, say, the
# closer slide and break the renderer's pattern branching.
_CROSS_PAPER_PATTERNS: frozenset[str] = frozenset({
    "title",
    "references",
    "bottlenecks_table",
    "proposed_direction_placeholder",
    "plan_numbered",
    "takeaway_closer",
})

# Patterns whose layouts do not use ``\frametitle`` (``title`` page uses
# ``\titlepage``; the closer uses a ``\rule`` divider). Every OTHER pattern
# is rendered as a content frame with ``\frametitle{<title>}`` — so an empty
# ``title`` on those patterns would silently emit ``\frametitle{}``. The
# schema must still accept ``title=""`` for the two legitimate cases, so the
# rule is conditional and enforced here, NOT via a ``min_length=1`` field
# constraint.
_TITLE_OPTIONAL_PATTERNS: frozenset[str] = frozenset({
    "title",
    "takeaway_closer",
})


@dataclass(frozen=True)
class _BriefSummary:
    """Compact per-paper summary recorded in ``args_redacted_json`` so a
    tracer reader can reconstruct what the planner saw without re-running
    T1. Mirrors the "record enough state to reconstruct" rule but stays
    small enough not to blow up the trace row."""

    paper_id: int
    contribution_len: int
    method_core_len: int
    key_results_count: int
    key_figures_count: int
    key_equations_count: int
    key_figure_keys: list[str]
    talk_shape_hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "contribution_len": self.contribution_len,
            "method_core_len": self.method_core_len,
            "key_results_count": self.key_results_count,
            "key_figures_count": self.key_figures_count,
            "key_equations_count": self.key_equations_count,
            "key_figure_keys": self.key_figure_keys,
            "talk_shape_hint": self.talk_shape_hint,
        }


def _summarise_brief(brief: PaperTalkBrief) -> _BriefSummary:
    return _BriefSummary(
        paper_id=brief.paper_id,
        contribution_len=len(brief.contribution),
        method_core_len=len(brief.method_core),
        key_results_count=len(brief.key_results),
        key_figures_count=len(brief.key_figures),
        key_equations_count=len(brief.key_equations),
        key_figure_keys=[kf.key for kf in brief.key_figures],
        talk_shape_hint=brief.talk_shape_hint,
    )


def _briefs_block(briefs: list[PaperTalkBrief]) -> str:
    """Render the briefs as a JSON array the LLM can read directly.

    Includes the load-bearing fields the planner needs to attribute slides
    (``paper_id``, ``key_figures[*].key`` + ``role``, ``key_equations``
    with the ``notation_explanation`` so the planner knows which equations
    are math-stack-eligible, and ``talk_shape_hint`` for per-paper slide
    budgeting).
    """
    blocks: list[dict[str, Any]] = []
    for idx, brief in enumerate(briefs):
        blocks.append(
            {
                "paper_idx": idx,
                "paper_id": brief.paper_id,
                "contribution": brief.contribution,
                "method_core": brief.method_core,
                "key_results": [kr.model_dump() for kr in brief.key_results],
                "key_figures": [kf.model_dump() for kf in brief.key_figures],
                "key_equations": [
                    {
                        "index": eq_idx,
                        "latex": ke.latex,
                        "role": ke.role,
                        "notation_explanation": ke.notation_explanation,
                    }
                    for eq_idx, ke in enumerate(brief.key_equations)
                ],
                "talk_shape_hint": brief.talk_shape_hint,
            }
        )
    return json.dumps(blocks, ensure_ascii=False, indent=2)


def _parse_outline(raw: str) -> DeckOutline:
    """Strip optional fence and validate against DeckOutline."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = _FENCE_RE.sub("", cleaned)
        cleaned = _FENCE_RE.sub("", cleaned).strip()
    return DeckOutline.model_validate_json(cleaned)


def _validate_attributions(
    outline: DeckOutline, briefs: list[PaperTalkBrief]
) -> None:
    """Walk every PlannedSlide and reject hallucinated attributions.

    Closes the renderer's burden: after this pass T3 may assume every
    PlannedSlide's ``paper_id`` / ``figure_key`` / ``equation_index`` is
    internally consistent with the input briefs. A mismatch raises
    ``ValueError`` (the tracer catches it, flips status to ``'error'``,
    and propagates).
    """
    paper_index: dict[int, PaperTalkBrief] = {b.paper_id: b for b in briefs}

    for slide_idx, slide in enumerate(outline.slides):
        # Content patterns MUST carry a non-empty title — T3 will render
        # them as ``\frametitle{<title>}``. Only ``title`` and
        # ``takeaway_closer`` use alternative framing (``\titlepage`` /
        # ``\rule``) so they may legitimately have ``title=""``. The
        # schema cannot enforce this conditionally; this check closes
        # the gap.
        if (
            slide.pattern_kind not in _TITLE_OPTIONAL_PATTERNS
            and not slide.title.strip()
        ):
            raise ValueError(
                f"planner emitted empty title for content pattern_kind="
                f"{slide.pattern_kind!r} at slide_index={slide_idx}; "
                "content patterns must carry a non-empty 2-6-word title "
                f"(only {sorted(_TITLE_OPTIONAL_PATTERNS)} may have title='')."
            )

        # Cross-paper patterns MUST have paper_id=null. A non-null
        # attribution on, e.g., the closer slide breaks T3's pattern
        # branching (the renderer would try to look up a paper-specific
        # figure on a cross-paper slot).
        if slide.pattern_kind in _CROSS_PAPER_PATTERNS:
            if slide.paper_id is not None:
                raise ValueError(
                    f"planner assigned paper_id={slide.paper_id} to "
                    f"cross-paper pattern_kind={slide.pattern_kind!r} "
                    f"at slide_index={slide_idx}; cross-paper slides "
                    "MUST have paper_id=null."
                )
            if slide.figure_key is not None:
                raise ValueError(
                    f"planner assigned figure_key={slide.figure_key!r} to "
                    f"cross-paper pattern_kind={slide.pattern_kind!r} "
                    f"at slide_index={slide_idx}; cross-paper slides "
                    "MUST have figure_key=null."
                )
            if slide.equation_index is not None:
                raise ValueError(
                    f"planner assigned equation_index={slide.equation_index} "
                    f"to cross-paper pattern_kind={slide.pattern_kind!r} "
                    f"at slide_index={slide_idx}; cross-paper slides "
                    "MUST have equation_index=null."
                )
            continue

        # Per-paper patterns: paper_id must resolve to a known brief.
        if slide.paper_id is None:
            raise ValueError(
                f"planner left paper_id=null on per-paper pattern_kind="
                f"{slide.pattern_kind!r} at slide_index={slide_idx}; "
                "per-paper slides must name their paper_id."
            )
        if slide.paper_id not in paper_index:
            valid_ids = sorted(paper_index)
            raise ValueError(
                f"planner assigned hallucinated paper_id={slide.paper_id} "
                f"at slide_index={slide_idx}; valid paper_ids={valid_ids}."
            )
        brief = paper_index[slide.paper_id]

        if slide.figure_key is not None:
            valid_keys = {kf.key for kf in brief.key_figures}
            if slide.figure_key not in valid_keys:
                raise ValueError(
                    f"planner assigned hallucinated figure_key="
                    f"{slide.figure_key!r} for paper_id={slide.paper_id} "
                    f"at slide_index={slide_idx}; valid keys="
                    f"{sorted(valid_keys)}."
                )

        if slide.equation_index is not None:
            n_eq = len(brief.key_equations)
            if not (0 <= slide.equation_index < n_eq):
                raise ValueError(
                    f"planner assigned invalid equation_index="
                    f"{slide.equation_index} for paper_id={slide.paper_id} "
                    f"at slide_index={slide_idx}; that paper has "
                    f"{n_eq} key_equations (valid indices 0..{n_eq - 1})."
                )


def _pattern_distribution(outline: DeckOutline) -> dict[str, int]:
    """Count slides per pattern_kind — recorded in result_summary_json so a
    trace reader can see the deck shape at a glance."""
    counts: dict[str, int] = {}
    for s in outline.slides:
        counts[s.pattern_kind] = counts.get(s.pattern_kind, 0) + 1
    return counts


def _paper_id_attribution(outline: DeckOutline) -> dict[str, int]:
    """Count slides per paper_id (with ``"cross"`` for null) — recorded so a
    trace reader can verify each paper got a fair share of attention."""
    counts: dict[str, int] = {}
    for s in outline.slides:
        key = "cross" if s.paper_id is None else str(s.paper_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


async def run_sl_plan_deck(
    *,
    briefs: list[PaperTalkBrief],
    target_slide_count: int,
    talk_title_hint: str | None,
    tracer: Tracer,
    model: str,
    deps: ReportDeps | None = None,
    response_language: str = "the user's language",
    memory_context: str = "",
    registry: PromptRegistry | None = None,
    **litellm_kwargs: Any,
) -> DeckOutline:
    """Plan the deck — single LLM call, structured output, validated.

    Returns a :class:`DeckOutline`. ``deps`` is accepted to match the T5
    wiring signature; T2 itself uses only the LLM model name + the prompt
    registry (no DB / retriever access — the briefs already encode the
    agentic read). Pass ``None`` from unit tests.
    """
    reg = registry or PromptRegistry()
    prompt = reg.get("slides_plan_deck/v1")

    paper_ids_in_brief = [b.paper_id for b in briefs]
    brief_summaries = [_summarise_brief(b).to_dict() for b in briefs]

    system = prompt.system.format(target_slide_count=target_slide_count)
    user = prompt.user_template.format(
        target_slide_count=target_slide_count,
        talk_title_hint=talk_title_hint or "(none provided — choose a title that fits the talk)",
        paper_count=len(briefs),
        briefs_block=_briefs_block(briefs),
        response_language=response_language or "the user's language",
        memory_context=memory_context,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    final_text: str = ""
    parse_error: str | None = None
    # If an exception fires inside the ``with`` block we still want the
    # canonical short marker ("plan_parse_failed" / "plan_validation_failed")
    # in the trace row — NOT the multi-kilobyte ValidationError message that
    # ``except Exception`` would otherwise stamp in there. We capture the
    # exception, exit the context cleanly with ``mark_error``, then re-raise
    # outside. The full exception detail still travels in
    # ``result_summary_json.parse_error`` so a debugger can reconstruct it
    # without re-running the LLM.
    pending_exc: Exception | None = None
    outline: DeckOutline | None = None

    async with tracer.step(
        agent="report",
        tool="report:plan_deck",
        model=model,
    ) as step:
        step.record_args(
            {
                "paper_ids_in_brief": paper_ids_in_brief,
                "target_slide_count": target_slide_count,
                "talk_title_hint": talk_title_hint,
                "brief_summary_per_paper": brief_summaries,
            }
        )

        # Note ``deps`` is intentionally unread inside T2 — accepted for the
        # T5 subgraph wiring signature so call sites do not need to branch
        # on plan vs render. ``_`` quiets linters in callers that pass deps.
        _ = deps

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            **litellm_kwargs,
        )
        msg = response["choices"][0]["message"]
        final_text = str(msg.get("content") or "").strip()

        try:
            outline = _parse_outline(final_text)
        except (ValidationError, ValueError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
            step.record_result(
                {
                    "final_text": final_text,
                    "final_text_len": len(final_text),
                    "parse_error": parse_error,
                }
            )
            step.mark_error("plan_parse_failed")
            pending_exc = exc

        if outline is not None:
            # Hallucination-rejection pass — raises ValueError on bad
            # attribution. Captured + re-raised outside the ``with`` so
            # the trace row carries the canonical short marker
            # ``"plan_validation_failed"`` instead of the long exception
            # text the tracer's ``except`` would otherwise record.
            try:
                _validate_attributions(outline, briefs)
            except ValueError as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
                step.record_result(
                    {
                        "final_text": final_text,
                        "final_text_len": len(final_text),
                        "parsed_outline_slides": [
                            s.model_dump() for s in outline.slides
                        ],
                        "validation_failed": True,
                        "validation_error": parse_error,
                    }
                )
                step.mark_error("plan_validation_failed")
                pending_exc = exc
                outline = None

        if outline is not None:
            step.record_result(
                {
                    "talk_title": outline.talk_title,
                    "talk_subtitle": outline.talk_subtitle,
                    "style_profile_name": outline.style_profile_name,
                    "planned_slides_count": len(outline.slides),
                    "pattern_kind_distribution": _pattern_distribution(outline),
                    "paper_id_attribution_counts": _paper_id_attribution(outline),
                    "slides": [s.model_dump() for s in outline.slides],
                    "final_text_len": len(final_text),
                    "parse_error": parse_error,
                }
            )

    if pending_exc is not None:
        raise pending_exc
    assert outline is not None  # control-flow invariant
    return outline
