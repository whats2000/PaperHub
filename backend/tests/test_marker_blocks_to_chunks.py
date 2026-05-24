"""Tests for block-anchored Marker chunk assembler (Plan F2.1 A2')."""
from __future__ import annotations

import json
from pathlib import Path

from paperhub.pipelines.marker_blocks_to_chunks import marker_blocks_to_chunks
from paperhub.pipelines.marker_client import _parse

_FIXTURE = Path(__file__).parent / "fixtures" / "marker_table_page.json"

_HEADERS = (
    "Layer Type",
    "Complexity per Layer",
    "Sequential",
    "Maximum Path Length",
)
_ROW_LABELS = (
    "Self-Attention",
    "Recurrent",
    "Convolutional",
    "Self-Attention (restricted)",
)


def _load_doc():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return _parse(payload)


def _table_chunk(chunks):
    for c in chunks:
        if "|" in c.text and all(h in c.text for h in _HEADERS):
            return c
    raise AssertionError("no chunk with the markdown table found")


def test_table_chunk_has_markdown_table_with_headers_and_rows() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    tc = _table_chunk(chunks)
    for h in _HEADERS:
        assert h in tc.text
    for label in _ROW_LABELS:
        assert label in tc.text
    # Real markdown table: pipes + a separator row.
    assert "|" in tc.text
    assert "---" in tc.text


def test_table_chunk_page_and_bbox() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    tc = _table_chunk(chunks)
    assert tc.page == 5
    assert tc.bbox is not None
    x0, y0, x1, y1 = tc.bbox
    # Within / approximately the TableGroup bbox [106.38, 70.77, 504.07, 185.625].
    assert 105.0 <= x0 <= 115.0
    assert 70.0 <= y0 <= 72.0
    assert 498.0 <= x1 <= 505.0
    assert 184.0 <= y1 <= 187.0


def test_no_standalone_tablecell_duplication() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    # The table appears exactly once across all chunk text — TableCell blocks
    # are skipped, so the row labels are not duplicated outside the table chunk.
    joined = "\n".join(c.text for c in chunks)
    assert joined.count("Self-Attention (restricted)") == 1


def test_table_chunk_section_inherited_not_none() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    tc = _table_chunk(chunks)
    assert tc.section is not None
    assert tc.section.strip() != ""


def test_match_text_has_no_markdown_markers() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    tc = _table_chunk(chunks)
    assert tc.match_text is not None
    assert "|" not in tc.match_text
    assert "#" not in tc.match_text
    assert "*" not in tc.match_text


def test_no_page_mixing_in_a_chunk() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    # Every chunk carries a single page (or None); pages 5 and 6 never share one.
    for c in chunks:
        assert c.page in (5, 6, None)
