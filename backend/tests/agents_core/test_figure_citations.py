"""``[fig:P/ID]`` citation markers: parsing and validation."""
from __future__ import annotations

from paperhub.agents.core.citations import (
    cited_figures,
    figure_key,
    validate_figure_citations,
)


def test_cited_figures_parses_single_and_list_markers() -> None:
    text = ("Four charts [fig:20/fig-003]. Curves [fig:53/fig-003, 53/fig-005] "
            "and [fig: 20 / fig-003, fig:25/fig-008].")
    assert cited_figures(text) == ["20/fig-003", "53/fig-003", "53/fig-005", "25/fig-008"]
    assert figure_key(20, "fig-003") == "20/fig-003"


def test_validate_drops_figures_not_viewed() -> None:
    text = "A [fig:20/fig-003]. B [fig:20/fig-003, 20/fig-009]. C [fig:9/fig-001]."
    out, removed = validate_figure_citations(text, {"20/fig-003"})
    assert out == "A [fig:20/fig-003]. B [fig:20/fig-003]. C [unsourced:figure_not_viewed]."
    assert removed == ["20/fig-009", "9/fig-001"]


def test_validate_leaves_chunk_markers_alone() -> None:
    text = "Value [chunk:5] and [fig:20/fig-003]."
    assert validate_figure_citations(text, {"20/fig-003"}) == (text, [])
