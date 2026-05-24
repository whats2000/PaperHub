# backend/src/paperhub/pipelines/marker_to_asset.py
"""Map a Marker MarkerDoc into the unified PaperAsset (SRS v2.19 §III-5.1).

Reconciled against the REAL marker JSON schema (verified via a live extract):
  * Figure captions live in a sibling ``Caption`` block, not the figure block's
    own html — the marker service pairs them into ``block.caption``.
  * ``section_hierarchy`` VALUES are block-id refs (e.g. "/page/1/SectionHeader/10"),
    NOT section names. We build a {block_id -> SectionHeader text} map from the
    SectionHeader blocks and resolve names through it.
  * Figure images are base64 JPEG or PNG — we sniff the magic bytes for the
    correct file extension so pdflatex's includegraphics resolves them.
"""
from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc
from paperhub.pipelines.paper_asset import (
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
)

_TAG = re.compile(r"<[^>]+>")


def strip_html(html: str) -> str:
    """Strip HTML tags to plain text (shared with the paper pipeline)."""
    return _TAG.sub("", html or "").strip()


def _image_ext(data: bytes) -> str:
    """Sniff the image format from magic bytes → file extension."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".png"  # default; pdflatex reads by extension


def _build_section_name_map(blocks: list[MarkerBlock]) -> dict[str, str]:
    """Map each SectionHeader block's id → its plain-text title.

    ``section_hierarchy`` on other blocks points at these ids, so this is how a
    figure/equation block recovers its owning section NAME.
    """
    out: dict[str, str] = {}
    for b in blocks:
        if b.block_type == "SectionHeader" and b.block_id:
            name = strip_html(b.html)
            if name:
                out[b.block_id] = name
    return out


def _resolve_section(block: MarkerBlock, name_map: dict[str, str]) -> str | None:
    """Resolve a block's deepest section_hierarchy ref to a section NAME."""
    sh = block.section_hierarchy or {}
    if not sh:
        return None
    deepest_ref = sh[max(sh.keys())]  # deepest level wins (keys are level numbers)
    return name_map.get(str(deepest_ref))


def marker_doc_to_markdown(doc: MarkerDoc) -> tuple[str, list[tuple[str, int]]]:
    """Assemble ALL Marker blocks (document order) into organized markdown +
    ``(section_name, char_offset)`` boundaries (Plan F2.1 Addendum A1).

    Replaces the figure-skipping ``_marker_text_and_sections``: figure captions,
    equations, and tables are emitted IN reading order so the chunker (and thus
    the embedder + answering model) see richer structured context. Pieces are
    joined with blank lines; a running char cursor tracks where each NEW section
    title first appears (boundaries are driven by ``SectionHeader`` blocks, same
    as the method it supersedes).

    Block rules:
      * SectionHeader → ``## {title}``; record ``(title, cursor)`` the first time
        each new title appears (``seen_sections`` dedup).
      * Figure / Picture → italic caption ``*{caption}*`` (caption = ``block.caption``,
        fallback ``strip_html(block.html)``); SKIP when empty. The raw caption is
        emitted verbatim (no "Figure:" prefix) so its stripped form matches the PDF.
      * Equation → ``$$ {latex} $$`` when ``block.latex``; else skip.
      * Table → ``strip_html(block.html)`` (plain cell text — a faithful markdown
        table is out of scope; cell text is what matters for RAG + matching).
      * Everything else (Text, ListItem, …) → ``strip_html(block.html)``; skip empty.
    """
    parts: list[str] = []
    boundaries: list[tuple[str, int]] = []
    seen_sections: set[str] = set()
    cursor = 0

    def _emit(piece: str) -> None:
        nonlocal cursor
        parts.append(piece)
        cursor += len(piece) + 2  # +2 for the "\n\n" separator joined below

    for block in doc.blocks:
        if block.block_type == "SectionHeader":
            title = strip_html(block.html)
            if not title:
                continue
            if title not in seen_sections:
                boundaries.append((title, cursor))
                seen_sections.add(title)
            _emit(f"## {title}")
        elif block.block_type in ("Figure", "Picture"):
            caption = block.caption if block.caption is not None else strip_html(block.html)
            caption = (caption or "").strip()
            if caption:
                _emit(f"*{caption}*")
        elif block.block_type == "Equation":
            if block.latex and block.latex.strip():
                _emit(f"$$ {block.latex.strip()} $$")
        else:
            piece = strip_html(block.html)
            if piece:
                _emit(piece)

    return "\n\n".join(parts), boundaries


def marker_doc_to_asset(doc: MarkerDoc, *, source_dir: Path) -> PaperAsset:
    figs_dir = paper_asset_dir(source_dir) / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)

    name_map = _build_section_name_map(doc.blocks)

    figures: list[FigureAsset] = []
    equations: list[EquationAsset] = []
    # Sections come straight from the SectionHeader blocks, in document order.
    sections: list[SectionAsset] = []
    seen_sections: set[str] = set()
    fig_n = eq_n = 0

    for block in doc.blocks:
        if block.block_type == "SectionHeader":
            name = strip_html(block.html)
            if name and name not in seen_sections:
                sections.append(SectionAsset(name=name, order=len(sections)))
                seen_sections.add(name)
            continue

        sec = _resolve_section(block, name_map)

        if block.block_type in ("Figure", "Picture") and block.images:
            raw = next(iter(block.images.values()))
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                continue
            fid = f"fig-{fig_n:03d}"
            fname = f"{fid}{_image_ext(data)}"
            (figs_dir / fname).write_bytes(data)
            # The service resolves the caption (often a sibling Caption block,
            # not the figure block's own html). Prefer it; fall back to html.
            caption = block.caption if block.caption is not None else strip_html(block.html)
            figures.append(
                FigureAsset(
                    id=fid,
                    caption=caption,
                    page=block.page,
                    section=sec,
                    image_path=f"figures/{fname}",
                )
            )
            fig_n += 1
        elif block.block_type == "Equation" and block.latex:
            equations.append(
                EquationAsset(id=f"eq-{eq_n:03d}", latex=block.latex.strip(), section=sec)
            )
            eq_n += 1

    return PaperAsset(figures=figures, equations=equations, sections=sections)
