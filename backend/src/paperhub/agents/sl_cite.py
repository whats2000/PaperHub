"""Write-time source-citation markers for slides (north-star traceback).

Every frame carries a ``% cite:`` LaTeX comment naming the source it was written
from — a real ``<paper_id>:<section_name>`` for a content slide, or a structural
sentinel (``title`` / ``divider`` / ``agenda``). The one-shot slide agent emits
the marker AS IT WRITES each frame (it has just read that section), so the
pipeline never "searches" to retrofit a citation — it only resolves the writer's
genuine declaration to chunks.

This module parses the markers and resolves them to ``SourceSection`` records so
``sl_emit`` (and the edit/restore paths) can persist per-slide grounding into
``deck_slides.source_sections_json``. Resolution is NON-BLOCKING: a cite that
names a section with no evidence resolves to an empty ``chunk_ids`` list (the
visible "unsourced" signal), never an error — generation is never gated on it.
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING, Any

from paperhub.agents.sl_read import read_section_chunks
from paperhub.models.slide_domain import SourceSection

if TYPE_CHECKING:
    from paperhub.db.deck_slides import DeckSlideInput

_CITE_RE = re.compile(
    r"^[ \t]*%[ \t]*cite:[ \t]*(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
_STRUCTURAL = {"title", "divider", "agenda"}


def serialize_cite(sources: list[tuple[int, str]]) -> str:
    """Build a ``% cite:`` marker line from ``(paper_id, section_name)`` pairs —
    the inverse of :func:`parse_cite`. Returns ``""`` when there are no sources
    (an unsourced / synthesis slide carries no marker)."""
    parts = [f"{pid}:{section}" for pid, section in sources if section]
    return f"% cite: {'; '.join(parts)}" if parts else ""

# One Beamer frame plus any ``% cite:`` comment line(s) immediately preceding
# ``\begin{frame}``. The agent may place the marker INSIDE the frame OR on the
# line just before it; frame extraction (build_deck_slides) keeps only the
# ``\begin{frame}…\end{frame}`` body, dropping a preceding comment — so grounding
# is resolved from the raw deck here, where the marker still travels with its
# frame. Group 2 is byte-identical to the extracted frame_tex, used as the join
# key (no fragile index alignment).
_FRAME_BLOCK_RE = re.compile(
    r"((?:[ \t]*%[ \t]*cite:[^\n]*\n\s*)?)(\\begin\{frame\}.*?\\end\{frame\})",
    re.DOTALL,
)


def frame_blocks(deck_tex: str) -> dict[str, str]:
    """Map each frame BODY to its full editable block — the frame plus any
    ``% cite:`` marker sitting just before ``\\begin{frame}`` (which frame
    extraction strips out of the stored ``frame_tex``). The per-frame editor
    loads the block, not the bare body, so the user SEES and CONTROLS the
    grounding marker (keep / edit / remove) rather than it being applied or
    dropped silently. An in-body marker is already inside the frame, so it
    rides along naturally."""
    return {
        m.group(2): m.group(1) + m.group(2)
        for m in _FRAME_BLOCK_RE.finditer(deck_tex)
    }


def parse_cite(frame_tex: str) -> tuple[str, list[tuple[int, str]]] | None:
    """Parse a frame's ``% cite:`` marker.

    Returns ``(kind, entries)`` where ``kind`` is
    "title"/"divider"/"agenda"/"hallucination"/"content" and ``entries`` is the
    list of ``(paper_id, section_name)`` for a content slide (empty otherwise).
    Returns ``None`` when the frame has no marker at all.
    """
    m = _CITE_RE.search(frame_tex)
    if m is None:
        return None
    body = m.group(1).strip()
    low = body.lower()
    if low == "hallucination":
        return "hallucination", []
    if low in _STRUCTURAL:
        return low, []
    entries: list[tuple[int, str]] = []
    for part in body.split(";"):
        pid_str, sep, section = part.partition(":")
        section = section.strip()
        if not sep or not section:
            continue
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        entries.append((pid, section))
    return "content", entries


async def frame_grounding(frame_tex: str, conn: Any) -> list[SourceSection]:
    """Resolve one frame's ``% cite:`` marker to its source sections + chunk ids.

    A content frame's cites resolve to chunks (``read_section_chunks``, which
    normalizes the section name); a structural / hallucination / unmarked frame
    returns ``[]``. A cite naming a section with no evidence resolves to a
    ``SourceSection`` with empty ``chunk_ids`` (recorded, not dropped — the
    non-blocking "unsourced" signal). Used by sl_emit AND the edit/restore paths
    so grounding is written identically everywhere and never lost on an edit.
    """
    parsed = parse_cite(frame_tex)
    if not parsed or parsed[0] != "content":
        return []
    out: list[SourceSection] = []
    for pid, section in parsed[1]:
        res = await read_section_chunks(
            paper_content_id=pid, section_name=section, conn=conn
        )
        out.append(
            SourceSection(
                paper_id=pid, section_name=section, chunk_ids=list(res.chunk_ids)
            )
        )
    return out


async def frame_grounding_json(frame_tex: str, conn: Any) -> str:
    """``frame_grounding`` serialized to the ``deck_slides.source_sections_json``
    shape (a JSON array of ``{paper_id, section_name, chunk_ids}``)."""
    return json.dumps(
        [ss.model_dump() for ss in await frame_grounding(frame_tex, conn)]
    )


async def with_grounding(
    slides: list[DeckSlideInput], deck_tex: str, conn: Any
) -> list[DeckSlideInput]:
    """Return ``slides`` with each one's ``source_sections_json`` resolved from
    its frame's ``% cite:`` marker. The single enrich point shared by every
    deck-write path (sl_emit GENERATE, the EDIT flow, the RESTORE flow) so
    grounding is computed identically everywhere and never dropped on an edit.

    Grounding is resolved from ``deck_tex`` (the full source), not from the
    stored ``frame_tex``: the agent often places the marker on the line just
    BEFORE ``\\begin{frame}``, which frame extraction strips out. Each frame is
    matched to its slide by exact frame-body equality (group 2 == frame_tex),
    so title-drop / ordering never misaligns the grounding."""
    # Keyed by exact frame-body string: two BYTE-IDENTICAL frames collapse to
    # one entry and share the last one's grounding. Harmless in practice —
    # structural frames resolve to [] and identical content frames carry
    # identical cites — but it is an assumption, not a guarantee.
    by_frame: dict[str, str] = {}
    for m in _FRAME_BLOCK_RE.finditer(deck_tex):
        block = m.group(1) + m.group(2)
        by_frame[m.group(2)] = await frame_grounding_json(block, conn)
    return [
        dataclasses.replace(
            s, source_sections_json=by_frame.get(s.frame_tex, "[]")
        )
        for s in slides
    ]
