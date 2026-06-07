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

# Parse a "Table N" / "Figure N" label out of a caption. Accepts "Fig", "Fig.",
# "Figure" (case-insensitive); normalizes to "Table N" / "Figure N".
_LABEL_RE = re.compile(r"^\s*\*?\s*(Table|Fig(?:ure)?\.?)\s*(\d+)", re.IGNORECASE)


def _parse_layout_label(caption: str | None) -> str | None:
    """Normalize a figure/table label from a caption, or ``None`` if absent.

    ``"Table 1: ..."`` → ``"Table 1"``; ``"Fig. 3 ..."`` / ``"Figure 3 ..."`` →
    ``"Figure 3"``. Case-insensitive on the keyword."""
    if not caption:
        return None
    m = _LABEL_RE.match(caption)
    if not m:
        return None
    kw = m.group(1).lower()
    kind = "Table" if kw.startswith("table") else "Figure"
    return f"{kind} {m.group(2)}"


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
        layout_kind: str | None = None,
        layout_caption: str | None = None,
    ) -> None:
        self.text = text
        self.section = section
        self.page = page
        self.bboxes = bboxes
        self.atomic = atomic  # TableGroup/Figure → never split, own chunk if needed
        # Layout-index provenance (F2.1 A3) — only on atomic table/figure pieces.
        self.layout_kind = layout_kind
        self.layout_caption = layout_caption


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
            text, bboxes, layout_kind, caption_text = _render_group(block, by_id)
            if not text:
                continue
            pieces.append(
                _Piece(
                    text,
                    section=piece_section,
                    page=block.page,
                    bboxes=bboxes,
                    atomic=True,
                    layout_kind=layout_kind,
                    layout_caption=caption_text,
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
            # Standalone table not folded into a group (rare). No caption block
            # is associated → layout_caption stays None (label unparsable).
            md = html_table_to_markdown(block.html)
            if md:
                pieces.append(
                    _Piece(
                        md,
                        section=current_section,
                        page=block.page,
                        bboxes=[block.bbox] if block.bbox else [],
                        atomic=True,
                        layout_kind="table",
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
                        layout_kind="figure",
                        layout_caption=caption,
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


def build_layout_index(chunks: list[tuple[Chunk, int]]) -> list[dict[str, object]]:
    """Build the per-paper layout index from ``(chunk, chunk_id)`` pairs.

    The worker assigns DB ids only AFTER inserting the chunks (RETURNING id),
    so this takes the chunk objects zipped with their freshly-assigned ids
    rather than reading ``chunk.id``. For each chunk tagged with a
    ``layout_kind`` (an atomic table/figure), emit
    ``{"kind", "label", "caption", "page", "chunk_id"}`` in document order.
    Untagged chunks are skipped."""
    index: list[dict[str, object]] = []
    for chunk, chunk_id in chunks:
        if chunk.layout_kind is None:
            continue
        index.append(
            {
                "kind": chunk.layout_kind,
                "label": chunk.layout_label,
                "caption": chunk.layout_caption,
                "page": chunk.page,
                "chunk_id": chunk_id,
            }
        )
    return index


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
) -> tuple[str, list[list[float]], str | None, str | None]:
    """Render a ``*Group`` block: caption (italic) above its table/figure.

    Returns ``(markdown_text, bboxes, layout_kind, caption_text)``:
      * the markdown text + the bboxes to union (the group's own bbox already
        covers caption+table);
      * ``layout_kind`` = ``"table"`` if the group contains a Table child, else
        ``"figure"`` for a Figure/Picture child (``None`` if neither);
      * ``caption_text`` = the plain caption (no markdown markers) for the
        layout index, or ``None``."""
    refs = _content_ref_ids(block.html)
    caption_md = ""
    body_md = ""
    caption_text: str | None = None
    layout_kind: str | None = None
    for ref in refs:
        child = by_id.get(ref)
        if child is None:
            continue
        if child.block_type == "Caption":
            cap = strip_html(child.html)
            if cap:
                caption_md = f"*{cap}*"
                caption_text = cap
        elif child.block_type == "Table":
            tbl = html_table_to_markdown(child.html)
            if tbl:
                body_md = tbl
            layout_kind = "table"
        elif child.block_type in ("Figure", "Picture"):
            cap = child.caption if child.caption is not None else strip_html(child.html)
            cap = (cap or "").strip()
            if cap and not caption_md:
                caption_md = f"*{cap}*"
                caption_text = cap
            if layout_kind is None:
                layout_kind = "figure"
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
    return text, bboxes, layout_kind, caption_text


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
        # An atomic piece always stands alone (flushed before + after), so a
        # single-piece atomic buffer carries the chunk's layout tags. A
        # multi-piece (running-text) buffer is never a layout object.
        layout_kind: str | None = None
        layout_label: str | None = None
        layout_caption: str | None = None
        if len(buf) == 1 and buf[0].layout_kind is not None:
            layout_kind = buf[0].layout_kind
            layout_caption = buf[0].layout_caption
            layout_label = _parse_layout_label(layout_caption)
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
                layout_kind=layout_kind,
                layout_label=layout_label,
                layout_caption=layout_caption,
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
        # disallowed_special=() so literal "<|endoftext|>" in the text (NLP
        # papers discuss it) is counted as tokens, not raised.
        return len(enc.encode(text, disallowed_special=()))

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
