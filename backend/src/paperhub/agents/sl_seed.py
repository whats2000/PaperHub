"""sl_seed — deterministic high-level paper map (F6.1, no LLM).

Builds a :class:`~paperhub.models.slide_domain.SeedPaper` for each
requested ``paper_content.id``:

* ``title`` / ``abstract`` — direct DB columns.
* ``sections`` — ``sections_json`` column if present and non-null; else
  falls back to ``DISTINCT section FROM chunks`` ordered by
  ``MIN(char_start)`` (stable, deterministic).
* ``figures`` — built from the paper's :class:`PaperAsset` via
  ``read_paper_asset(source_dir_path)``; keys use the same
  ``p{idx}-{fig_id}`` scheme as :func:`figure_inventory.build_inventory`.
  Degrades to an empty list when the asset is absent or unreadable.
* ``is_survey`` — pure heuristic, no model calls.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

from paperhub.models.slide_domain import SeedFigure, SeedPaper
from paperhub.pipelines.paper_asset import read_paper_asset

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure heuristic
# ---------------------------------------------------------------------------

_SURVEY_TOKENS = (
    "survey",
    "a review",
    "review of",
    "comprehensive review",
    "overview of",
    "taxonomy of",
)


def _looks_like_survey(title: str, abstract: str) -> bool:
    """Return True when *title* or *abstract* signal a survey / review paper."""
    haystack = (title + " " + abstract).lower()
    return any(tok in haystack for tok in _SURVEY_TOKENS)


def section_names_from_json(sections_json_raw: str | None) -> list[str]:
    """Extract ordered section NAMES from a ``paper_content.sections_json`` value.

    The real column is a JSON list of ``{name, char_start, char_end,
    token_count, chunk_count}`` dicts; defensively we also accept a list of
    bare strings (older rows / fixtures). Returns ``[]`` on absent/malformed
    input so callers can fall back to the chunk-derived sections.
    """
    if not sections_json_raw:
        return []
    try:
        parsed = json.loads(sections_json_raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    names: list[str] = []
    for s in parsed:
        if isinstance(s, dict):
            name = s.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        elif isinstance(s, str) and s.strip():
            names.append(s.strip())
    return names


# ---------------------------------------------------------------------------
# Figure extraction helpers
# ---------------------------------------------------------------------------


def _figures_for_paper(paper_id: int, source_dir_raw: str | None) -> list[SeedFigure]:
    """Return ``SeedFigure`` list for one paper.  Empty on any failure."""
    if not source_dir_raw:
        return []
    source_dir = Path(source_dir_raw)
    if not source_dir.exists():
        return []
    try:
        asset = read_paper_asset(source_dir)
    except Exception:  # noqa: BLE001
        log.debug("sl_seed: could not read asset for paper %d", paper_id)
        return []
    if asset is None:
        return []
    # Key scheme matches figure_inventory.build_inventory: p{idx}-{fig_id}.
    # For sl_seed, idx is not meaningful (we don't have a global deck index here),
    # so we use "p0-" as a stable prefix — the outline stage will re-key against
    # the real inventory when it builds the deck.  The caption is what matters.
    return [
        SeedFigure(key=f"p0-{fig.id}", caption=fig.caption)
        for fig in asset.figures
    ]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_sl_seed(
    *,
    paper_ids: list[int],
    conn: aiosqlite.Connection,
) -> list[SeedPaper]:
    """Return one :class:`SeedPaper` per requested ``paper_content.id``.

    Results are returned in the same order as *paper_ids*.  Unknown ids are
    silently skipped (the caller should ensure they exist).
    """
    if not paper_ids:
        return []

    # Fetch title, abstract, sections_json, source_dir_path in one query.
    placeholders = ",".join("?" for _ in paper_ids)
    async with conn.execute(
        f"SELECT id, title, abstract, sections_json, source_dir_path "
        f"FROM paper_content WHERE id IN ({placeholders})",
        paper_ids,
    ) as cur:
        rows = {row[0]: row for row in await cur.fetchall()}

    # Fallback section query: chunk sections ordered by first occurrence.
    async def _chunk_sections(pid: int) -> list[str]:
        async with conn.execute(
            "SELECT section FROM chunks "
            "WHERE paper_content_id = ? AND section IS NOT NULL AND section != '' "
            "GROUP BY section ORDER BY MIN(char_start)",
            (pid,),
        ) as c2:
            return [r[0] for r in await c2.fetchall()]

    result: list[SeedPaper] = []
    for pid in paper_ids:
        row = rows.get(pid)
        if row is None:
            log.warning("sl_seed: paper_id %d not found in paper_content", pid)
            continue

        _id, title, abstract, sections_json_raw, source_dir_raw = row

        # --- sections ---
        names = section_names_from_json(sections_json_raw)
        sections: list[str] = names if names else await _chunk_sections(pid)

        # --- figures ---
        figures = _figures_for_paper(pid, source_dir_raw)

        result.append(
            SeedPaper(
                paper_id=pid,
                title=str(title),
                abstract=str(abstract),
                is_survey=_looks_like_survey(str(title), str(abstract)),
                sections=sections,
                figures=figures,
            )
        )

    return result
