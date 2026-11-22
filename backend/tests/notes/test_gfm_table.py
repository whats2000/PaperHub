"""GFM pipe tables <-> a table block's ``tableContent`` (H5b.1 Task 5)."""
from __future__ import annotations

import pytest

from paperhub.notes.gfm_table import parse_gfm_table, table_to_gfm
from paperhub.notes.models import check_table, column_aligns, table_rows


def _texts(content: dict) -> list[list[str]]:
    return [["".join(r.get("text", "") for r in cell) for cell in row]
            for row in table_rows(content)]


def test_parse_simple_table() -> None:
    content = parse_gfm_table("| a | b |\n|---|---|\n| 1 | 2 |")
    check_table(content)
    assert content["type"] == "tableContent"
    assert content["headerRows"] == 1
    assert content["columnWidths"] == [None, None]
    assert _texts(content) == [["a", "b"], ["1", "2"]]


@pytest.mark.parametrize("text", [
    "| a | b | c |\n| --- | :-: | --: |\n| 1 | 2 | 3 |",
    "| 指標 | 值 |\n| --- | --- |\n| 準確率 | 62% |",
    "| a | b |\n| --- | --- |\n|  | x |",
    "| a \\| b | c |\n| --- | --- |\n| $\\vert x \\vert$ | y |",
    "| only |\n| --- |",
])
def test_round_trip(text: str) -> None:
    content = parse_gfm_table(text)
    check_table(content)
    assert table_to_gfm(content) == text
    assert parse_gfm_table(table_to_gfm(content)) == content


def test_alignment_read_from_delimiter_row() -> None:
    content = parse_gfm_table("| a | b | c | d |\n|---|:-:|--:|:--|\n|1|2|3|4|")
    # Left is the editor's default alignment, written as ---.
    assert column_aligns(content) == ["", "center", "right", ""]
    assert table_to_gfm(content).splitlines()[1] == "| --- | :-: | --: | --- |"


def test_escaped_pipe_is_cell_text() -> None:
    content = parse_gfm_table("| a \\| b | c |\n|---|---|\n| x | y |")
    assert _texts(content)[0] == ["a | b", "c"]


def test_outer_pipes_optional_and_spaces_trimmed() -> None:
    content = parse_gfm_table("a | b\n--- | ---\n  1  |  2  ")
    assert _texts(content) == [["a", "b"], ["1", "2"]]


def test_empty_cells() -> None:
    content = parse_gfm_table("| a | b |\n|---|---|\n|  | 2 |")
    assert table_rows(content)[1][0] == []


def test_cjk_cells() -> None:
    content = parse_gfm_table("| 方法 | 結果 |\n|---|---|\n| 專家選擇 | 更快 |")
    assert _texts(content) == [["方法", "結果"], ["專家選擇", "更快"]]


@pytest.mark.parametrize(("text", "reason"), [
    ("| a | b |\n| 1 | 2 |", "delimiter row"),
    ("| a | b |", "delimiter row"),
    ("", "delimiter row"),
    ("| a | b |\n|---|---|\n| 1 | 2 | 3 |", "row 2 has 3 cells"),
    ("| a | b |\n|---|---|\n| 1 |", "row 2 has 1 cell"),
    ("| a | b |\n|---|\n| 1 | 2 |", "delimiter row"),
])
def test_rejects_with_reason(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        parse_gfm_table(text)


def test_to_gfm_reads_rich_cells_like_runs() -> None:
    content = {"type": "tableContent", "columnWidths": [None, None],
               "headerRows": 1, "rows": [
                   {"cells": ["k", "v"]},
                   {"cells": [[{"type": "text", "text": "ORR",
                                "styles": {"bold": True}}],
                              [{"type": "inlineMath", "props": {"latex": "a|b"}},
                               {"type": "markCite",
                                "props": {"markId": "m1", "text": "src"}}]]}]}
    assert table_to_gfm(content) == "| k | v |\n| --- | --- |\n| ORR | $a\\|b$src |"


def test_to_gfm_folds_line_breaks() -> None:
    content = {"type": "tableContent", "rows": [
        {"cells": ["h"]}, {"cells": ["one\ntwo"]}]}
    assert table_to_gfm(content) == "| h |\n| --- |\n| one two |"
