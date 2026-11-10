"""Tests for core-agent citation validation (H4 Task 9)."""
from __future__ import annotations

from paperhub.agents.core.citations import cited_ids, validate_citations


def test_keeps_allowed_and_removes_unread_marker() -> None:
    text = "Loss is L [chunk:5]. Optimiser is Adam [chunk:9]."
    clean, removed = validate_citations(text, {5})
    assert clean == "Loss is L [chunk:5]. Optimiser is Adam [unsourced:not_read]."
    assert removed == [9]


def test_multi_id_marker_keeps_only_allowed_members() -> None:
    clean, removed = validate_citations("A [chunk:1,2, 3] b", {1, 3})
    assert clean == "A [chunk:1,3] b"
    assert removed == [2]


def test_all_members_removed_drops_marker() -> None:
    clean, removed = validate_citations("A [chunk:7,8] b [chunk:7]", set())
    assert clean == "A [unsourced:not_read] b [unsourced:not_read]"
    assert removed == [7, 8]


def test_no_markers_unchanged() -> None:
    assert validate_citations("plain text", {1}) == ("plain text", [])


def test_cited_ids_in_order_unique() -> None:
    assert cited_ids("x [chunk:3] y [chunk:1,3] z [chunk: 2]") == [3, 1, 2]


def test_repeated_prefix_marker_is_validated_and_normalised() -> None:
    """Models sometimes write ``[chunk:1, chunk:2]``; the UI only renders
    ``[chunk:1,2]``, and an unread member must not slip past validation."""
    clean, removed = validate_citations("A [chunk:1, chunk:2] b [chunk:1, chunk:3]", {1, 3})
    assert clean == "A [chunk:1] b [chunk:1,3]"
    assert removed == [2]
    assert cited_ids("x [chunk:4, chunk:5]") == [4, 5]
