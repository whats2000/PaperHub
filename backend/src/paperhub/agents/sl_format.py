"""Shared slide-context formatting helpers.

Both the deterministic Base Writer (``sl_base_write``) and the revise agent
(``slide_agent``) render the same context shape (outline + bundles + figure
inventory) into their prompts. These formatters live here so the two stages
share one source of truth instead of cross-importing from each other.
"""
from __future__ import annotations

import json
from typing import Any, Final

from paperhub.models.slide_domain import (
    DeckOutline,
    KeyFigureBundle,
    PaperContextBundle,
)

_EXCERPT_MAX_CHARS: Final[int] = 300


def _format_bundles_block(bundles: list[PaperContextBundle]) -> str:
    rows: list[dict[str, Any]] = []
    for b in bundles:
        rows.append(
            {
                "paper_idx": b.paper_idx,
                "title": b.title,
                "authors": b.authors[:5],
                "year": b.year,
                "narrative_summary": b.narrative_summary,
                "key_figures": [
                    {
                        "key": f.key,
                        "role": f.role,
                        "interp": f.one_line_interpretation,
                        "aspect": round(f.dimensions.aspect_ratio, 2),
                    }
                    for f in b.key_figures
                ],
                "key_equations": [
                    {
                        "latex": e.latex[:200],
                        "role": e.role,
                        "notation": e.notation_legend[:100],
                    }
                    for e in b.key_equations
                ],
                "section_excerpts": [
                    {"section": s.section_name, "text": s.text[:600]}
                    for s in b.section_excerpts
                ],
                "paper_newcommands": b.paper_newcommands[:30],
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _format_figure_inventory_block(inv: dict[str, KeyFigureBundle]) -> str:
    if not inv:
        return "(empty — no figures)"
    return "\n".join(
        f"- {key}: aspect={fig.dimensions.aspect_ratio:.2f} "
        f"({fig.dimensions.width_px}x{fig.dimensions.height_px}) role={fig.role}"
        for key, fig in inv.items()
    )


def _format_outline_block(outline: DeckOutline | None) -> str:
    """Render the approved outline for the drafter. The drafter MUST render
    exactly one frame per slide, in order — no add/drop/split (the 1:1 contract
    that keeps grounding mapped to pages by slide_index).

    Each slide line now includes:
    - ``[form: <content_form>]`` so the drafter knows the intended render style.
    - An ``Evidence:`` sub-list of ``support_excerpts`` (truncated to
      ``_EXCERPT_MAX_CHARS`` chars) so the drafter writes from fetched material
      rather than hallucinating. Omitted when the slide has no excerpts.
    """
    if outline is None:
        return ""
    lines = [
        "## APPROVED TALK OUTLINE — render EXACTLY one frame per slide below, "
        "in this order. Do NOT add, drop, merge, or split slides.",
        f"Talk title: {outline.talk_title}",
        f"Audience intent: {outline.audience_intent}",
        f"Narrative arc: {outline.narrative_arc}",
        "",
        "Slides:",
    ]
    for s in outline.slides:
        fig = f" [figure: {s.figure_key}]" if s.figure_key else ""
        msg = f" — {s.key_message}" if s.key_message else ""
        form = f" [form: {s.content_form}]"
        lines.append(f"{s.slide_index + 1}. {s.goal}{msg}{form}{fig}")
        if s.support_excerpts:
            lines.append("   Evidence:")
            for excerpt in s.support_excerpts:
                if len(excerpt) > _EXCERPT_MAX_CHARS:
                    excerpt = excerpt[:_EXCERPT_MAX_CHARS] + "..."
                lines.append(f"   - {excerpt}")
    return "\n".join(lines)
