# backend/src/paperhub/pipelines/marker_blocks_to_chunks.py
"""Block-anchored chunk assembler for Marker-extracted PDFs (Plan F2.1 A2').

The agentic RAG flow has the model READ each chunk's markdown, so layout
matters (tables as REAL markdown tables, equations as ``$$``, figure captions).
Each chunk is a group of consecutive Marker blocks sharing one
``(section, page)`` rendered to markdown, carrying the union ``bbox`` + ``page``
of its blocks so the Citation Canvas can draw the highlight GEOMETRICALLY
(exact), not by text-matching.

Why block-anchored (vs the old ``marker_doc_to_markdown`` + ``strip_html``):
that path flattened tables (destroyed rows/columns) and duplicated them via the
redundant ``TableCell`` blocks. Here, ``TableGroup`` folds its child
``Caption`` + ``Table`` into one atomic markdown piece and every ``TableCell``
is skipped unconditionally.
"""
from __future__ import annotations

import re
from collections.abc import Callable

import tiktoken

from paperhub.pipelines.chunker import Chunk
from paperhub.pipelines.html_to_markdown import html_table_to_markdown
from paperhub.pipelines.markdown_strip import strip_markdown
from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc
from paperhub.pipelines.marker_to_asset import (
    _build_section_name_map,
    _resolve_section,
    strip_html,
)

# Reuse the chunker's tokenizer + budget. Block-anchored pieces are kept whole
# (never split mid-block), so a chunk fills up to the ~800-token target and
# closes before exceeding the 1000-token hard cap.
_TARGET = 800
_HARD = 1000

_CONTENT_REF_RE = re.compile(r"content-ref\s+src=['\"]([^'\"]+)['\"]")
_GROUP_TYPES = ("TableGroup", "FigureGroup", "PictureGroup")
_SKIP_TYPES = ("PageFooter", "PageHeader")


def _content_ref_ids(html: str) -> list[str]:
    """Parse the child block-ids a Group block references via ``content-ref``."""
    return _CONTENT_REF_RE.findall(html or "")


def _bbox_union(
    bboxes: list[list[float]],
) -> tuple[float, float, float, float] | None:
    """Union (min x0,y0 / max x1,y1) of valid 4-tuple bboxes; ``None`` if none."""
    valid = [b for b in bboxes if b and len(b) >= 4]
    if not valid:
        return None
    x0 = min(b[0] for b in valid)
    y0 = min(b[1] for b in valid)
    x1 = max(b[2] for b in valid)
    y1 = max(b[3] for b in valid)
    return (x0, y0, x1, y1)


class _Piece:
    """A single emitted block rendered to a markdown piece + its provenance."""

    def __init__(
        self,
        text: str,
        *,
        section: str | None,
        page: int | None,
        bboxes: list[list[float]],
        atomic: bool,
    ) -> None:
        self.text = text
        self.section = section
        self.page = page
        self.bboxes = bboxes
        self.atomic = atomic  # TableGroup/Figure → never split, own chunk if needed


def marker_blocks_to_chunks(doc: MarkerDoc) -> list[Chunk]:
    """Assemble Marker blocks into block-anchored chunks (one section+page each).

    Rules:
      * Build ``{block_id -> MarkerBlock}`` + the SectionHeader name map.
      * Children consumed by a ``*Group`` block (via ``content-ref``) — and ALL
        ``TableCell`` blocks — are not emitted standalone.
      * Walk in document order, tracking ``current_section`` (resolve the deepest
        ``section_hierarchy`` ref; empty → keep last-seen). A ``*Group``/figure
        with no resolvable section inherits the NEXT block's section (floated
        tables are emitted before their surrounding text in Marker's order).
      * Render each emitted block to a markdown piece; group consecutive pieces
        with the same ``(section, page)`` up to the token budget; an atomic
        (table/figure) piece flushes the current chunk first and stands alone if
        it would overflow. Pages never mix in one chunk.
    """
    blocks = doc.blocks
    name_map = _build_section_name_map(blocks)
    by_id: dict[str, MarkerBlock] = {b.block_id: b for b in blocks if b.block_id}

    # Children folded into a Group block → not emitted standalone.
    consumed: set[str] = set()
    for b in blocks:
        if b.block_type in _GROUP_TYPES:
            for ref in _content_ref_ids(b.html):
                consumed.add(ref)

    # Resolve each block's section once (deepest hierarchy ref → name).
    def _sec(b: MarkerBlock) -> str | None:
        return _resolve_section(b, name_map)

    pieces: list[_Piece] = []
    current_section: str | None = None

    for idx, block in enumerate(blocks):
        bt = block.block_type
        if bt in _SKIP_TYPES:
            continue
        if bt == "TableCell":
            continue
        if block.block_id and block.block_id in consumed:
            continue

        # Update current_section from this block's hierarchy when present.
        resolved = _sec(block)
        if resolved:
            current_section = resolved

        if bt == "SectionHeader":
            title = strip_html(block.html)
            if not title:
                continue
            current_section = title
            pieces.append(
                _Piece(
                    f"## {title}",
                    section=current_section,
                    page=block.page,
                    bboxes=[block.bbox] if block.bbox else [],
                    atomic=False,
                )
            )
            continue

        if bt in _GROUP_TYPES:
            piece_section = resolved or current_section
            if piece_section is None:
                piece_section = _lookahead_section(blocks, idx, _sec)
            text, bboxes = _render_group(block, by_id)
            if not text:
                continue
            pieces.append(
                _Piece(
                    text,
                    section=piece_section,
                    page=block.page,
                    bboxes=bboxes,
                    atomic=True,
                )
            )
            continue

        if bt == "Equation":
            if block.latex and block.latex.strip():
                pieces.append(
                    _Piece(
                        f"$$ {block.latex.strip()} $$",
                        section=current_section,
                        page=block.page,
                        bboxes=[block.bbox] if block.bbox else [],
                        atomic=False,
                    )
                )
            continue

        if bt == "Table":
            # Standalone table not folded into a group (rare).
            md = html_table_to_markdown(block.html)
            if md:
                pieces.append(
                    _Piece(
                        md,
                        section=current_section,
                        page=block.page,
                        bboxes=[block.bbox] if block.bbox else [],
                        atomic=True,
                    )
                )
            continue

        if bt in ("Figure", "Picture"):
            caption = block.caption if block.caption is not None else strip_html(block.html)
            caption = (caption or "").strip()
            if caption:
                piece_section = current_section
                if piece_section is None:
                    piece_section = _lookahead_section(blocks, idx, _sec)
                pieces.append(
                    _Piece(
                        f"*{caption}*",
                        section=piece_section,
                        page=block.page,
                        bboxes=[block.bbox] if block.bbox else [],
                        atomic=True,
                    )
                )
            continue

        # Text / ListItem / default.
        piece = strip_html(block.html)
        if piece:
            pieces.append(
                _Piece(
                    piece,
                    section=current_section,
                    page=block.page,
                    bboxes=[block.bbox] if block.bbox else [],
                    atomic=False,
                )
            )

    return _group_pieces_to_chunks(pieces)


def _lookahead_section(
    blocks: list[MarkerBlock],
    idx: int,
    sec_fn: Callable[[MarkerBlock], str | None],
) -> str | None:
    """First resolvable section among the blocks AFTER ``idx`` (floated-table
    fallback: Marker emits a TableGroup before its surrounding section text)."""
    for b in blocks[idx + 1 :]:
        if b.block_type == "SectionHeader":
            title = strip_html(b.html)
            if title:
                return title
        s = sec_fn(b)
        if s:
            return s
    return None


def _render_group(
    block: MarkerBlock, by_id: dict[str, MarkerBlock]
) -> tuple[str, list[list[float]]]:
    """Render a ``*Group`` block: caption (italic) above its table/figure.

    Returns the markdown text + the bboxes to union (the group's own bbox, which
    already covers caption+table)."""
    refs = _content_ref_ids(block.html)
    caption_md = ""
    body_md = ""
    for ref in refs:
        child = by_id.get(ref)
        if child is None:
            continue
        if child.block_type == "Caption":
            cap = strip_html(child.html)
            if cap:
                caption_md = f"*{cap}*"
        elif child.block_type == "Table":
            tbl = html_table_to_markdown(child.html)
            if tbl:
                body_md = tbl
        elif child.block_type in ("Figure", "Picture"):
            cap = child.caption if child.caption is not None else strip_html(child.html)
            cap = (cap or "").strip()
            if cap and not caption_md:
                caption_md = f"*{cap}*"
    parts = [p for p in (caption_md, body_md) if p]
    text = "\n\n".join(parts)
    # The group's own bbox covers caption+table; prefer it (matches the desired
    # geometric highlight). Fall back to a union of child bboxes.
    bboxes: list[list[float]] = [block.bbox] if block.bbox else []
    if not bboxes:
        for ref in refs:
            child = by_id.get(ref)
            if child and child.bbox:
                bboxes.append(child.bbox)
    return text, bboxes


def _group_pieces_to_chunks(pieces: list[_Piece]) -> list[Chunk]:
    """Group consecutive pieces sharing ``(section, page)`` into token-budgeted
    chunks; atomic pieces flush first and stand alone. Computes real running
    char offsets across the whole concatenated document text."""
    enc = tiktoken.get_encoding("cl100k_base")
    chunks: list[Chunk] = []
    cursor = 0  # running offset into the whole-document text (chunks joined later)

    buf: list[_Piece] = []

    def _flush() -> None:
        nonlocal cursor
        if not buf:
            return
        text = "\n\n".join(p.text for p in buf)
        bboxes: list[list[float]] = []
        for p in buf:
            bboxes.extend(p.bboxes)
        char_start = cursor
        char_end = cursor + len(text)
        chunks.append(
            Chunk(
                section=buf[0].section,
                char_start=char_start,
                char_end=char_end,
                text=text,
                dom_id=None,
                match_text=strip_markdown(text),
                page=buf[0].page,
                bbox=_bbox_union(bboxes),
            )
        )
        # +2 accounts for the "\n\n" that would join this chunk to the next in
        # the whole-document text (mirrors the old assembler's cursor stepping).
        cursor = char_end + 2
        buf.clear()

    def _buf_tokens(extra: str | None = None) -> int:
        text = "\n\n".join(p.text for p in buf)
        if extra is not None:
            text = (text + "\n\n" + extra) if text else extra
        return len(enc.encode(text))

    for piece in pieces:
        same_group = bool(buf) and buf[-1].section == piece.section and buf[-1].page == piece.page
        if piece.atomic:
            # Atomic pieces (table/figure) never merge into a running text chunk
            # and never share a chunk with other pieces — flush, then stand alone.
            _flush()
            buf.append(piece)
            _flush()
            continue
        if not buf:
            buf.append(piece)
            continue
        if not same_group:
            _flush()
            buf.append(piece)
            continue
        # Close once the buffer has reached the soft target (keeps blocks whole,
        # never splitting mid-block) and always before the hard cap.
        if buf and _buf_tokens() >= _TARGET or _buf_tokens(piece.text) > _HARD:
            _flush()
            buf.append(piece)
            continue
        buf.append(piece)

    _flush()
    return chunks
