"""Plan H2 fix C1: one shared parser for the app's chunk-citation markers."""
from __future__ import annotations

from paperhub.feedback.citations import cited_chunk_ids


def test_single_marker() -> None:
    assert cited_chunk_ids("A [chunk:7] (p.1).") == [7]


def test_multi_id_marker() -> None:
    assert cited_chunk_ids("A [chunk:101,102].") == [101, 102]


def test_spaced_multi_id_marker() -> None:
    assert cited_chunk_ids("A [chunk: 101, 102 ].") == [101, 102]


def test_repeated_chunk_prefix_marker() -> None:
    assert cited_chunk_ids("A [chunk:1, chunk:2].") == [1, 2]


def test_order_preserving_dedupe_across_markers() -> None:
    text = "x [chunk:5] y [chunk:3,5] z [chunk: 9, chunk:3]"
    assert cited_chunk_ids(text) == [5, 3, 9]


def test_no_marker() -> None:
    assert cited_chunk_ids("no citations, chunk 4 [p.2]") == []
