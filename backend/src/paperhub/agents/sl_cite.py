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

import json
import re
from typing import Any

from paperhub.agents.sl_read import read_section_chunks
from paperhub.models.slide_domain import SourceSection

_CITE_RE = re.compile(
    r"^[ \t]*%[ \t]*cite:[ \t]*(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
_STRUCTURAL = {"title", "divider", "agenda"}


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
