"""Tests for html_table_to_markdown (Plan F2.1 A2')."""
from __future__ import annotations

from paperhub.pipelines.html_to_markdown import html_table_to_markdown


def _rows(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if ln.strip()]


def test_header_th_and_td_rows() -> None:
    html = (
        "<table><tbody>"
        "<tr><th>A</th><th>B</th></tr>"
        "<tr><td>1</td><td>2</td></tr>"
        "<tr><td>3</td><td>4</td></tr>"
        "</tbody></table>"
    )
    md = html_table_to_markdown(html)
    rows = _rows(md)
    assert rows[0] == "| A | B |"
    # Separator row with the right column count.
    assert rows[1] == "| --- | --- |"
    assert rows[2] == "| 1 | 2 |"
    assert rows[3] == "| 3 | 4 |"


def test_br_replaced_with_space() -> None:
    html = "<table><tr><th>X</th></tr><tr><td>a<br>b</td></tr></table>"
    md = html_table_to_markdown(html)
    rows = _rows(md)
    assert rows[0] == "| X |"
    assert rows[2] == "| a b |"


def test_first_tr_as_header_when_no_th() -> None:
    html = "<table><tr><td>h1</td><td>h2</td></tr><tr><td>v1</td><td>v2</td></tr></table>"
    md = html_table_to_markdown(html)
    rows = _rows(md)
    assert rows[0] == "| h1 | h2 |"
    assert rows[1] == "| --- | --- |"
    assert rows[2] == "| v1 | v2 |"


def test_ragged_rows_padded_and_truncated() -> None:
    html = (
        "<table>"
        "<tr><th>A</th><th>B</th><th>C</th></tr>"
        "<tr><td>1</td></tr>"  # short → padded
        "<tr><td>x</td><td>y</td><td>z</td><td>extra</td></tr>"  # long → truncated
        "</table>"
    )
    md = html_table_to_markdown(html)
    rows = _rows(md)
    assert rows[0] == "| A | B | C |"
    assert rows[2] == "| 1 |  |  |"
    assert rows[3] == "| x | y | z |"


def test_empty_cells_and_tag_stripping() -> None:
    html = (
        "<table><tr><th>A</th><th>B</th></tr>"
        "<tr><td><b>bold</b></td><td></td></tr></table>"
    )
    md = html_table_to_markdown(html)
    rows = _rows(md)
    assert rows[2] == "| bold |  |"
