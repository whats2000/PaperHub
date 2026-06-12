"""paper_digest — cached per-section insight builder (F6.1-R gather rework).

Compresses each paper ONCE into a cheap :class:`PaperDigest` (per-section
1–2 line insight, built by the SMALL model) so the outline orchestrator can
structure the full deck from the digest and then request only targeted reads
for exact evidence.

Public API
----------
build_paper_digest(*, paper_id, conn, asset, adapter, model) -> PaperDigest
    Build fresh — fetches sections from DB, runs parallel per-section insight
    calls, assembles the digest.  Degrades per section on model failure.

get_or_build_digest(*, paper_id, conn, asset, adapter, model) -> PaperDigest
    Cache-aware wrapper.  Reads ``<source_dir>/digest.json`` when present and
    valid; builds + writes on cache miss or corrupt/stale file.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.agents.sl_seed import _figures_for_paper, section_names_from_json
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.slide_domain import (
    DigestEquation,
    DigestSection,
    PaperDigest,
    SeedFigure,
)
from paperhub.pipelines.paper_asset import PaperAsset

log = logging.getLogger(__name__)

# Maximum characters fed to the model per section (keeps the digest cheap).
_SECTION_TEXT_CAP = 4000

# Maximum equations carried into the digest (keeps the payload small).
_EQUATIONS_CAP = 6


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_paper_row(
    paper_id: int, conn: aiosqlite.Connection
) -> tuple[str, str, str | None, str | None] | None:
    """Return (title, abstract, sections_json, source_dir_path) or None."""
    async with conn.execute(
        "SELECT title, abstract, sections_json, source_dir_path "
        "FROM paper_content WHERE id = ?",
        (paper_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1]), row[2], row[3]


async def _chunk_sections(paper_id: int, conn: aiosqlite.Connection) -> list[str]:
    """Fallback: return distinct section names ordered by first char_start."""
    async with conn.execute(
        "SELECT section FROM chunks "
        "WHERE paper_content_id = ? AND section IS NOT NULL AND section != '' "
        "GROUP BY section ORDER BY MIN(char_start)",
        (paper_id,),
    ) as cur:
        return [r[0] for r in await cur.fetchall()]


async def _section_text(
    paper_id: int, section_name: str, conn: aiosqlite.Connection
) -> str:
    """Fetch and join chunk text for one section, capped to _SECTION_TEXT_CAP chars."""
    async with conn.execute(
        "SELECT text FROM chunks "
        "WHERE paper_content_id = ? AND section = ? "
        "ORDER BY char_start",
        (paper_id, section_name),
    ) as cur:
        rows = await cur.fetchall()
    joined = "\n".join(r[0] for r in rows if r[0])
    return joined[:_SECTION_TEXT_CAP]


async def _insight_for_section(
    *,
    paper_id: int,
    name: str,
    conn: aiosqlite.Connection,
    adapter: LlmAdapter,
    model: str,
) -> DigestSection:
    """Return a DigestSection for one named section.

    On any model-CALL failure, degrades to DigestSection(name=name, insight="")
    so a single bad section never aborts the whole digest.
    """
    text = await _section_text(paper_id, name, conn)
    try:
        out: Any = await adapter.structured(
            slot="slides_paper_digest/v1",
            variables={"section_name": name, "section_text": text},
            response_model=DigestSection,
            model=model,
        )
        # Trust the KNOWN name; only take the model's insight.
        return DigestSection(name=name, insight=str(out.insight).strip())
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "paper_digest: insight call failed for paper=%d section=%r — degrading: %s",
            paper_id,
            name,
            exc,
        )
        return DigestSection(name=name, insight="")


def _equations_from_asset(asset: PaperAsset | None) -> list[DigestEquation]:
    """Map EquationAsset list → DigestEquation list (role=""), capped at _EQUATIONS_CAP."""
    if asset is None:
        return []
    return [
        DigestEquation(latex=eq.latex, role="")
        for eq in asset.equations[:_EQUATIONS_CAP]
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_paper_digest(
    *,
    paper_id: int,
    conn: aiosqlite.Connection,
    asset: PaperAsset | None,
    adapter: LlmAdapter,
    model: str,
    _prefetched_row: tuple[str, str, str | None, str | None] | None = None,
) -> PaperDigest:
    """Build a fresh :class:`PaperDigest` for *paper_id*.

    Steps
    -----
    1. Fetch title / abstract / sections_json / source_dir_path from
       ``paper_content`` (one query).  Callers that already hold the row may
       pass it via *_prefetched_row* to avoid a second DB round-trip.
    2. Derive section names from ``sections_json`` or fall back to the
       chunk-derived list.
    3. Run per-section insight calls in parallel (asyncio.gather).
       Each section degrades independently on failure.
    4. Build figures from the paper asset; equations from *asset* (cap 6).
    5. Return the assembled PaperDigest.
    """
    row = _prefetched_row if _prefetched_row is not None else await _fetch_paper_row(paper_id, conn)
    if row is None:
        log.warning("paper_digest: paper_id %d not found in paper_content", paper_id)
        return PaperDigest(
            paper_id=paper_id,
            title="",
            abstract="",
            sections=[],
            figures=[],
            key_equations=[],
        )

    title, abstract, sections_json_raw, source_dir_raw = row

    # --- section names ---
    names = section_names_from_json(sections_json_raw)
    if not names:
        names = await _chunk_sections(paper_id, conn)

    # --- parallel per-section insight calls (degrade individually) ---
    section_coros = [
        _insight_for_section(
            paper_id=paper_id,
            name=name,
            conn=conn,
            adapter=adapter,
            model=model,
        )
        for name in names
    ]
    # return_exceptions=True so one failure doesn't abort the gather.
    # _insight_for_section's inner try guards only the MODEL call; the
    # gather-level isinstance(result, BaseException) fallback below covers
    # unexpected failures including DB-level ones (_section_text is outside
    # the per-section try and can propagate here).
    results = await asyncio.gather(*section_coros, return_exceptions=True)
    sections: list[DigestSection] = []
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            log.debug(
                "paper_digest: unexpected exception for section %r: %s", name, result
            )
            sections.append(DigestSection(name=name, insight=""))
        else:
            sections.append(result)

    # --- figures ---
    figures: list[SeedFigure] = _figures_for_paper(paper_id, source_dir_raw)

    # --- equations ---
    key_equations = _equations_from_asset(asset)

    return PaperDigest(
        paper_id=paper_id,
        title=title,
        abstract=abstract,
        sections=sections,
        figures=figures,
        key_equations=key_equations,
    )


async def get_or_build_digest(
    *,
    paper_id: int,
    conn: aiosqlite.Connection,
    asset: PaperAsset | None,
    adapter: LlmAdapter,
    model: str,
) -> PaperDigest:
    """Return a cached :class:`PaperDigest`, building and writing it if necessary.

    Cache location: ``<source_dir_path>/digest.json``.  Co-located with the
    ``asset/`` directory — this dir is per-``content_key``, so the cache is
    shared across sessions and survives paper cache-hits, matching the
    file-based PaperAsset convention.

    Fallback: when ``source_dir_path`` is absent / empty the digest is built
    fresh on every call (no crash, no cache write).  On a corrupt/stale
    ``digest.json`` the file is ignored and the digest is rebuilt.
    """
    # Resolve source_dir_path for the cache anchor.
    row = await _fetch_paper_row(paper_id, conn)
    source_dir_raw: str | None = row[3] if row is not None else None

    cache_path: Path | None = None
    if source_dir_raw:
        cache_path = Path(source_dir_raw) / "digest.json"

    # --- cache hit ---
    if cache_path is not None and cache_path.exists():
        try:
            digest = PaperDigest.model_validate_json(
                cache_path.read_text(encoding="utf-8")
            )
            return digest
        except Exception:  # noqa: BLE001
            log.debug(
                "paper_digest: corrupt/stale digest.json for paper=%d — rebuilding",
                paper_id,
            )

    # --- cache miss / invalid ---
    digest = await build_paper_digest(
        paper_id=paper_id,
        conn=conn,
        asset=asset,
        adapter=adapter,
        model=model,
        _prefetched_row=row,
    )

    # Write cache when we have an anchor dir (atomic: write temp then replace)
    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".json.tmp")
            tmp.write_text(digest.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(cache_path)
        except Exception:  # noqa: BLE001
            log.debug(
                "paper_digest: could not write digest cache for paper=%d", paper_id
            )

    return digest
