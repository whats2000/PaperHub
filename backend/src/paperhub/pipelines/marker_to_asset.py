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
    BlockAsset,
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
    write_block_assets,
)
from paperhub.pipelines.section_names import is_heading_shaped, is_wrapped_continuation

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

    Marker's layout model also files non-headings as SectionHeader: chart axis
    tick runs, sub-figure labels ("(b) Reach with Cup"), and the second line
    of a wrapped heading. A wrapped continuation (lowercase, short, right after
    another header) is joined onto the header before it; any other text that
    is not heading-shaped is left out, so its blocks keep the enclosing
    section (``_resolve_section`` -> None -> the caller keeps the last one).
    """
    out: dict[str, str] = {}
    prev_header: str | None = None  # id of the header block just before
    for b in blocks:
        if b.block_type != "SectionHeader" or not b.block_id:
            prev_header = None
            continue
        name = strip_html(b.html)
        if not name:
            prev_header = None
            continue
        if prev_header is not None and is_wrapped_continuation(name):
            joined = f"{out[prev_header]} {name}"
            for bid, v in list(out.items()):
                if v == out[prev_header]:
                    out[bid] = joined
            out[b.block_id] = joined
            prev_header = b.block_id
            continue
        if is_heading_shaped(name):
            out[b.block_id] = name
            prev_header = b.block_id
        else:
            prev_header = None
    return out


def section_header_name(block: MarkerBlock, name_map: dict[str, str]) -> str | None:
    """The section NAME a SectionHeader block opens, or None when it is not a
    real heading (see :func:`_build_section_name_map`)."""
    if block.block_id:
        return name_map.get(block.block_id)
    name = strip_html(block.html)
    return name if name and is_heading_shaped(name) else None


def _resolve_section(block: MarkerBlock, name_map: dict[str, str]) -> str | None:
    """Resolve a block's deepest section_hierarchy ref to a section NAME."""
    sh = block.section_hierarchy or {}
    if not sh:
        return None
    deepest_ref = sh[max(sh.keys())]  # deepest level wins (keys are level numbers)
    return name_map.get(str(deepest_ref))


def _clean(s: str) -> str:
    """Drop lone UTF-16 surrogates (bad OCR) that UTF-8 JSON output cannot encode."""
    return s.encode("utf-8", "ignore").decode("utf-8")


def marker_doc_to_blocks(doc: MarkerDoc) -> list[BlockAsset]:
    """Every Marker block in reading order (page chrome included; filtered later)."""
    name_map = _build_section_name_map(doc.blocks)
    out: list[BlockAsset] = []
    for b in doc.blocks:
        raw = [float(v) for v in (b.bbox or [])[:4]]
        bbox = (raw[0], raw[1], raw[2], raw[3]) if len(raw) == 4 else (0.0, 0.0, 0.0, 0.0)
        out.append(BlockAsset(
            block_id=b.block_id or "",
            block_type=b.block_type,
            page=b.page if b.page is not None else 0,
            bbox=bbox,
            html=_clean(b.html),
            section=_resolve_section(b, name_map),
        ))
    return out


def marker_doc_to_asset(
    doc: MarkerDoc, *, source_dir: Path, inline_math_llm: bool = False,
) -> PaperAsset:
    write_block_assets(source_dir, marker_doc_to_blocks(doc), inline_math_llm=inline_math_llm)
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
            name = section_header_name(block, name_map)
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
