"""Tests for block-anchored Marker chunk assembler (Plan F2.1 A2')."""
from __future__ import annotations

import json
from pathlib import Path

from paperhub.pipelines.chunker import Chunk
from paperhub.pipelines.marker_blocks_to_chunks import (
    build_layout_index,
    marker_blocks_to_chunks,
)
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


# ---------------------------------------------------------------------------
# F2.1 A3: layout tagging + build_layout_index
# ---------------------------------------------------------------------------


def test_table_chunk_is_layout_tagged() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    tc = _table_chunk(chunks)
    assert tc.layout_kind == "table"
    assert tc.layout_label == "Table 1"
    assert tc.layout_caption is not None
    assert "Maximum path" in tc.layout_caption


def test_non_layout_chunks_untagged() -> None:
    chunks = marker_blocks_to_chunks(_load_doc())
    # Text chunks (e.g. the "Why Self-Attention" prose) carry no layout tags.
    text_chunks = [c for c in chunks if c.layout_kind is None]
    assert text_chunks  # there is plenty of prose
    for c in text_chunks:
        assert c.layout_label is None
        assert c.layout_caption is None


def test_build_layout_index_emits_tagged_entries() -> None:
    tagged = Chunk(
        section="3.4 Embeddings",
        char_start=0,
        char_end=10,
        text="*Table 1: foo*\n\n| a | b |",
        page=5,
        layout_kind="table",
        layout_label="Table 1",
        layout_caption="Table 1: foo",
    )
    fig = Chunk(
        section="2 Background",
        char_start=20,
        char_end=30,
        text="*Figure 2: bar*",
        page=3,
        layout_kind="figure",
        layout_label="Figure 2",
        layout_caption="Figure 2: bar",
    )
    plain = Chunk(section="1", char_start=40, char_end=50, text="prose")
    index = build_layout_index([(tagged, 101), (plain, 102), (fig, 103)])
    assert index == [
        {
            "kind": "table",
            "label": "Table 1",
            "caption": "Table 1: foo",
            "page": 5,
            "chunk_id": 101,
        },
        {
            "kind": "figure",
            "label": "Figure 2",
            "caption": "Figure 2: bar",
            "page": 3,
            "chunk_id": 103,
        },
    ]


def test_build_layout_index_unlabeled_entry_keeps_kind() -> None:
    fig = Chunk(
        section="2",
        char_start=0,
        char_end=5,
        text="*An uncaptioned schematic*",
        page=7,
        layout_kind="figure",
        layout_label=None,
        layout_caption="An uncaptioned schematic",
    )
    index = build_layout_index([(fig, 9)])
    assert index == [
        {
            "kind": "figure",
            "label": None,
            "caption": "An uncaptioned schematic",
            "page": 7,
            "chunk_id": 9,
        }
    ]
