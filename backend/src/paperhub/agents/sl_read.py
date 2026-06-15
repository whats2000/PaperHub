"""sl_read — deterministic read_section worker (F6.1-R, no LLM).

Fetches all chunks belonging to one ``(paper_content_id, section_name)``
pair in ``char_start`` order, joins their texts, and caps the result at
:data:`_READ_TEXT_CAP` characters.  No model calls, fully deterministic.
"""
from __future__ import annotations

import aiosqlite
from pydantic import BaseModel, ConfigDict

# Maximum number of characters returned for any section.
# Keeps the result within a safe context window for the outline orchestrator.
_READ_TEXT_CAP: int = 6000


class ReadResult(BaseModel):
    """The text and contributing chunk IDs for a single section fetch."""

    model_config = ConfigDict(extra="forbid")

    text: str
    """Joined chunk text (capped at :data:`_READ_TEXT_CAP` characters)."""

    chunk_ids: list[int]
    """IDs of the chunks that contributed, in ``char_start`` order."""


def _norm_section(section: str) -> str:
    """Whitespace-collapse + lowercase a section name for lenient matching.

    The agent's ``% cite:`` marker may differ from the canonical DB section
    only by spacing/case; normalizing both lets the resolution succeed instead
    of shipping an empty (unsourced) grounding for a section that really exists.
    """
    return " ".join(section.split()).lower()


async def read_section_chunks(
    *,
    paper_content_id: int,
    section_name: str,
    conn: aiosqlite.Connection,
) -> ReadResult:
    """Return the joined text and chunk ids for one section of one paper.

    Queries ``chunks`` by ``(paper_content_id, section)`` ordered by
    ``char_start``.  Returns :class:`ReadResult` with empty ``text`` and
    ``chunk_ids`` when no matching rows exist (unknown paper or section).

    Parameters
    ----------
    paper_content_id:
        The ``paper_content.id`` to filter on (``chunks.paper_content_id``).
    section_name:
        The exact ``chunks.section`` value to fetch.
    conn:
        An open :class:`aiosqlite.Connection` with the PaperHub schema.
    """
    async with conn.execute(
        "SELECT id, text FROM chunks "
        "WHERE paper_content_id = ? AND section = ? "
        "ORDER BY char_start",
        (paper_content_id, section_name),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        # Normalized fallback: resolve the canonical DB section whose normalized
        # form matches, so a cite that differs only in spacing/case still
        # grounds to real chunks rather than shipping empty (unsourced).
        target = _norm_section(section_name)
        async with conn.execute(
            "SELECT DISTINCT section FROM chunks WHERE paper_content_id = ?",
            (paper_content_id,),
        ) as cur:
            sections = [r[0] for r in await cur.fetchall() if r[0] is not None]
        canonical = next(
            (s for s in sections if _norm_section(str(s)) == target), None
        )
        if canonical is None:
            return ReadResult(text="", chunk_ids=[])
        async with conn.execute(
            "SELECT id, text FROM chunks "
            "WHERE paper_content_id = ? AND section = ? "
            "ORDER BY char_start",
            (paper_content_id, canonical),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return ReadResult(text="", chunk_ids=[])

    chunk_ids = [int(row[0]) for row in rows]
    joined = "\n".join(str(row[1]) for row in rows)
    text = joined[:_READ_TEXT_CAP]

    return ReadResult(text=text, chunk_ids=chunk_ids)
