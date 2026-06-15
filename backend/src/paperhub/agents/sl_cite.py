"""Write-time source-citation markers for slides (north-star traceback).

Every frame carries a ``% cite:`` LaTeX comment naming the source it was written
from — a real ``<paper_id>:<section_name>`` for a content slide, or a structural
sentinel (``title`` / ``divider`` / ``agenda``). This module parses those markers,
validates them (a content slide MUST cite a real section that has evidence; a
structural marker must match a genuinely structural slide), and exposes the
per-frame content cites so ``sl_emit`` can persist deck_slides grounding.

The marker is captured AT WRITE TIME by the base writer / revise agent — the
pipeline never "searches" to retrofit a citation; it only resolves the writer's
genuine declaration to chunks.
"""
from __future__ import annotations

import json
import re
from typing import Any

from paperhub.agents.sl_read import read_section_chunks
from paperhub.models.slide_domain import CiteViolationSignal, SourceSection

_FRAME_SPAN_RE = re.compile(r"\\begin\{frame\}.*?\\end\{frame\}", re.DOTALL)
_FRAMETITLE_RE = re.compile(r"\\begin\{frame\}\s*(?:\[[^\]]*\])?\s*\{(.*?)\}", re.DOTALL)
_CITE_RE = re.compile(r"^[ \t]*%[ \t]*cite:[ \t]*(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
# Content scaffolding that means a frame is NOT a bare structural slide.
_CONTENT_ENV_RE = re.compile(
    r"\\includegraphics|\\begin\{(?:tabular|tikzpicture|itemize|enumerate|"
    r"equation|align|align\*|gather)\}|\\smartdiagram|\\\[",
)
_STRUCTURAL = {"title", "divider", "agenda"}


async def load_valid_sections(
    paper_ids: list[int], conn: Any
) -> set[tuple[int, str]]:
    """Return ``{(paper_id, normalized_section)}`` that actually have chunks.

    This is the EVIDENCE set: a cite is valid only if its (paper, section) has
    real chunks. Built once from the ``chunks`` table; the cite gate rejects any
    reference to a paper_id or section not in it (no evidence = hallucination).
    """
    if not paper_ids or conn is None:
        return set()
    placeholders = ",".join("?" for _ in paper_ids)
    valid: set[tuple[int, str]] = set()
    async with conn.execute(
        f"SELECT DISTINCT paper_content_id, section FROM chunks "  # noqa: S608 — ints only
        f"WHERE paper_content_id IN ({placeholders})",
        list(paper_ids),
    ) as cur:
        async for row in cur:
            if row[1] is not None:
                valid.add((int(row[0]), _norm_section(str(row[1]))))
    return valid


def _frame_title(frame_tex: str) -> str:
    m = _FRAMETITLE_RE.search(frame_tex)
    return (m.group(1).strip() if m else "")[:80]


def _norm_section(section: str) -> str:
    return " ".join(section.split()).lower()


def parse_cite(frame_tex: str) -> tuple[str, list[tuple[int, str]]] | None:
    """Parse a frame's ``% cite:`` marker.

    Returns ``(kind, entries)`` where kind is "title"/"divider"/"agenda"/"content"
    and entries is the list of ``(paper_id, section_name)`` for content slides
    (empty for structural). Returns ``None`` when there is no marker.
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

    A content frame's cites resolve to chunks (read_section_chunks, normalized);
    a structural / hallucination / unmarked frame returns []. Used by BOTH
    sl_emit (GENERATE) and the edit path so deck_slides grounding is written the
    same way everywhere and never dropped on an edit."""
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
    """``frame_grounding`` serialized to the deck_slides.source_sections_json shape."""
    return json.dumps([ss.model_dump() for ss in await frame_grounding(frame_tex, conn)])


def content_cites(deck_tex: str) -> list[tuple[int, list[tuple[int, str]]]]:
    """Per content frame (0-based over \\begin{frame}), its cited (paper_id,
    section) entries. Structural / unmarked frames are returned with an empty
    list so the caller can align by frame index."""
    out: list[tuple[int, list[tuple[int, str]]]] = []
    for idx, m in enumerate(_FRAME_SPAN_RE.finditer(deck_tex)):
        parsed = parse_cite(m.group(0))
        if parsed and parsed[0] == "content":
            out.append((idx, parsed[1]))
        else:
            out.append((idx, []))
    return out


def detect_cite_violations(
    deck_tex: str, valid_sections: set[tuple[int, str]]
) -> list[CiteViolationSignal]:
    """Validate every frame's source-cite marker.

    ``valid_sections`` is the set of ``(paper_id, normalized_section_name)`` that
    actually have chunks (= have evidence). Violations:
      - ``missing``: no ``% cite:`` marker at all.
      - ``fake_structural``: a title/divider marker on a frame that has real
        content (dodging citation).
      - ``content_uncited``: a content slide with no content cite.
      - ``no_evidence``: a cited section is not among the paper's real sections.
    """
    # No evidence set => nothing to ground against (no chunks loaded for these
    # papers); the gate stands down. In production every ingested paper has
    # chunks, so valid_sections is non-empty and the gate is fully active.
    if not valid_sections:
        return []
    violations: list[CiteViolationSignal] = []
    for idx, m in enumerate(_FRAME_SPAN_RE.finditer(deck_tex)):
        frame = m.group(0)
        title = _frame_title(frame)
        parsed = parse_cite(frame)
        has_content = bool(_CONTENT_ENV_RE.search(frame))
        if parsed is None:
            violations.append(
                CiteViolationSignal(
                    frame_index=idx, frame_title=title, reason="missing",
                    detail="no '% cite:' marker on this frame",
                )
            )
            continue
        kind, entries = parsed
        if kind == "hallucination":
            # base_write labels an unsourced content slide as a hallucination;
            # the revise agent MUST replace it with a real source.
            violations.append(
                CiteViolationSignal(
                    frame_index=idx, frame_title=title, reason="hallucination",
                    detail="slide labelled '% cite: hallucination' — read a real "
                    "section and replace with % cite: <paper_id>:<section>",
                )
            )
            continue
        if kind in _STRUCTURAL:
            # A title marker must be a real \titlepage; a divider/agenda marker
            # must not carry real content (figure/table/math/list).
            if kind == "title" and "\\titlepage" not in frame:
                violations.append(
                    CiteViolationSignal(
                        frame_index=idx, frame_title=title, reason="fake_structural",
                        detail="cite:title but the frame has no \\titlepage",
                    )
                )
            elif kind in {"divider", "agenda"} and has_content:
                violations.append(
                    CiteViolationSignal(
                        frame_index=idx, frame_title=title, reason="fake_structural",
                        detail=f"cite:{kind} but the frame has real content",
                    )
                )
            continue
        # content
        if not entries:
            violations.append(
                CiteViolationSignal(
                    frame_index=idx, frame_title=title, reason="content_uncited",
                    detail="content slide with no '<paper_id>:<section>' cite",
                )
            )
            continue
        for pid, section in entries:
            if (pid, _norm_section(section)) not in valid_sections:
                violations.append(
                    CiteViolationSignal(
                        frame_index=idx, frame_title=title, reason="no_evidence",
                        detail=f"cited section has no evidence: {pid}:{section}",
                    )
                )
    return violations
