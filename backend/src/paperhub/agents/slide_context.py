"""Active-slide context for slide-aware QA (SRS v2.29).

When the composer's slide chip is attached, ``build_slide_context`` hands the
model the **full LaTeX source of the on-screen slide verbatim** (equations,
text, items, everything — we do NOT pre-select what the model "needs"), plus
the captions for any ``\\includegraphics`` keys (which are opaque on their own).
This anchors paper_qa onto exactly what is on the slide — e.g. "explain this
formula" sees the actual formula, not a guessed one. Returns ``None`` (→ plain
paper_qa, no regression) whenever there is no deck / no on-screen page / no
matching row. ``slide_aware_query`` prepends the block to the resolved query
for both the subagent and the finalizer.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import aiosqlite

from paperhub.agents.state import AgentState, effective_query
from paperhub.db.deck_slides import get_deck_slides
from paperhub.db.decks import get_deck
from paperhub.pipelines.slide_pipeline.figure_inventory import build_inventory

_GRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")


def _frame_figure_keys(frame_tex: str) -> list[str]:
    return [Path(m.group(1)).stem for m in _GRAPHICS_RE.finditer(frame_tex)]


async def _enabled_paper_keys(
    conn: aiosqlite.Connection, session_id: int
) -> list[dict[str, object]]:
    # Same ordering (added_at) build_inventory used at deck-build time so the
    # p{idx}-{fig.id} keys reverse correctly.
    async with conn.execute(
        "SELECT pc.id, pc.source_dir_path FROM papers p "
        "JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 ORDER BY p.added_at",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"id": r[0], "source_dir": r[1]} for r in rows]


async def build_slide_context(
    conn: aiosqlite.Connection, *, session_id: int, current_view_page: int | None
) -> str | None:
    """Return the active-slide context block, or ``None`` when not applicable.

    Caller gates on the chip's ``slide_attached`` flag BEFORE calling this; this
    function additionally returns None for the no-deck / no-page / no-row cases.
    """
    if current_view_page is None or current_view_page <= 0:
        return None
    deck = await get_deck(conn, session_id=session_id)
    if deck is None:
        return None
    rows = await get_deck_slides(conn, deck_id=deck.id)
    row = next((r for r in rows if r.page_start <= current_view_page <= r.page_end), None)
    if row is None:
        return None

    lines = [
        "The user is currently viewing the slide below (page "
        f"{current_view_page}) in a presentation deck generated from the "
        "reference paper(s). Its FULL LaTeX source follows verbatim. Answer the "
        "user's question about EXACTLY what is on THIS slide (the specific "
        "equation, figure, or statement shown), grounding details in the "
        "paper(s). Do NOT substitute a different equation/figure from the paper "
        "than the one on the slide. Citation discipline: attach a [chunk:<id>] "
        "marker ONLY to a claim the cited chunk actually states. A fact shown "
        "on the slide but NOT found in the retrieved paper text must be "
        "attributed to the slide in prose (e.g. \"as shown on the slide\") with "
        "NO [chunk:<id>] marker — never fabricate a citation for it. Do NOT "
        "assert specific details (units, numeric values, definitions) that are "
        "neither shown on the slide NOR in the retrieved paper text — if the "
        "slide does not specify something, say so rather than inventing it.",
        "--- BEGIN SLIDE LATEX ---",
        row.frame_tex.strip(),
        "--- END SLIDE LATEX ---",
    ]

    fig_keys = _frame_figure_keys(row.frame_tex)
    if fig_keys:
        papers = await _enabled_paper_keys(conn, session_id)
        inventory = await asyncio.to_thread(build_inventory, papers)
        by_key = {f.key: f.caption for f in inventory}
        captions = [f"{k}: {by_key[k]}" for k in fig_keys if k in by_key]
        if captions:
            lines.append(
                "Captions for the \\includegraphics keys on this slide — "
                + " | ".join(captions)
            )
    return "\n".join(lines)


def slide_aware_query(state: AgentState) -> str:
    """The QA query: the idea context of a chat opened from an idea and the
    active-slide context (when present) prepended to the router's resolved
    query, so section navigation targets the right section. Falls back to
    ``effective_query`` when there is neither."""
    base = effective_query(state)
    blocks = [b for b in (state.get("idea_context"), state.get("slide_context")) if b]
    if not blocks:
        return base
    return "\n\n".join(blocks) + f"\n\nThe user's question: {base}"
