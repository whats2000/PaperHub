"""A citation that fails validation is not deleted silently: it becomes a
visible [unsourced:<reason>] mark, so the reader knows the claim's source
could not be checked. A marker that keeps a valid source gets no mark."""
from __future__ import annotations

from paperhub.agents.core.citations import (
    UNSOURCED_RE,
    validate_citations,
    validate_figure_citations,
    validate_view_citations,
    verify_quotes,
)


def test_unread_chunk_becomes_a_mark() -> None:
    out, removed = validate_citations("A [chunk:5]. B [chunk:9]. C [chunk:5, 9].", {5})
    assert out == "A [chunk:5]. B [unsourced:not_read]. C [chunk:5]."
    assert removed == [9]


def test_unviewed_figure_and_region_become_marks() -> None:
    out, _ = validate_figure_citations("F [fig:3/fig-001]. G [fig:3/fig-002].", {"3/fig-001"})
    assert out == "F [fig:3/fig-001]. G [unsourced:figure_not_viewed]."
    out, _, _ = validate_view_citations("V [view:3/2/0,0,10,10].", set())
    assert out == "V [unsourced:region_not_viewed]."


def test_quote_found_nowhere_becomes_a_mark() -> None:
    text = 'It "runs at 120 FPS on a Jetson Nano" [chunk:1]. Next.'
    out, fixes = verify_quotes(text, {1: "Die defect inspection performance."})
    assert out == 'It "runs at 120 FPS on a Jetson Nano" [unsourced:quote_not_found]. Next.'
    assert fixes[0]["action"] == "marked"


def test_mark_pattern() -> None:
    assert [m.group(1) for m in UNSOURCED_RE.finditer(
        "a [unsourced:not_read] b [unsourced: quote_not_found]")] == [
        "not_read", "quote_not_found"]
