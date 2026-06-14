"""Deterministic base-deck writer: outline + bundles + preamble -> deck.tex.

One generation (no tools); the whole streamed response IS the deck. The revise
agent (``slide_agent``) then compiles + polishes it. Traced as
``report:base_write``.

Reuses the outline/bundle/figure-inventory formatting helpers from
``sl_format`` so the base writer and the revise agent render the same context
shape from one source of truth.
"""
from __future__ import annotations

import re

from paperhub.agents.sl_format import (
    _format_bundles_block,
    _format_figure_inventory_block,
    _format_outline_block,
)
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.slide_domain import (
    DeckOutline,
    KeyFigureBundle,
    PaperContextBundle,
)
from paperhub.tracing.tracer import Tracer

# Strips a leading ```latex / ```tex / ``` fence and a trailing ``` fence the
# model may wrap the deck in despite the "no fences" instruction.
_FENCE_LEAD = re.compile(r"^```(?:latex|tex)?[ \t]*\r?\n?", re.IGNORECASE)
_FENCE_TRAIL = re.compile(r"\r?\n?```$")


def _strip_fence(s: str) -> str:
    s = s.strip()
    s = _FENCE_LEAD.sub("", s)
    s = _FENCE_TRAIL.sub("", s)
    return s.strip()


async def run_base_write(
    *,
    outline: DeckOutline,
    bundles: list[PaperContextBundle],
    resolved_preamble: str,
    response_language: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    task_description: str = "",
    figure_inventory_block: str = "",
    figure_inventory: dict[str, KeyFigureBundle] | None = None,
) -> str:
    """Generate the COMPLETE base deck.tex in a single (tool-free) pass.

    ``figure_inventory_block`` is the pre-rendered inventory string; if it is
    empty and ``figure_inventory`` is given, it is rendered from that dict so
    the caller can hand either form.
    """
    if not figure_inventory_block and figure_inventory:
        figure_inventory_block = _format_figure_inventory_block(figure_inventory)

    async with tracer.step(
        agent="report", tool="report:base_write", model=model
    ) as step:
        step.record_args(
            {
                "n_slides": len(outline.slides),
                "n_bundles": len(bundles),
                "task": task_description[:200],
            }
        )
        parts: list[str] = []
        async for tok in adapter.stream(
            slot="slides_base_write/v1",
            variables={
                "task_description": task_description,
                "response_language": response_language,
                "resolved_preamble": resolved_preamble,
                "outline_block": _format_outline_block(outline),
                "bundles_block": _format_bundles_block(bundles),
                "n_bundles": len(bundles),
                "figure_inventory_block": figure_inventory_block,
            },
            model=model,
        ):
            parts.append(tok)
        deck = _strip_fence("".join(parts))
        step.record_result(
            {
                "deck_len": len(deck),
                "n_frames": deck.count("\\begin{frame}"),
            }
        )
        return deck
