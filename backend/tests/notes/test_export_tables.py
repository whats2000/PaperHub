"""A table block exports as a Markdown pipe table and a Word table."""
from __future__ import annotations

import io
import re
import shutil
import zipfile
from typing import Any

import pytest

from paperhub.notes.export import _cited, note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import markdown_to_docx

NOTE = Note(id=1, target_kind="session", target_id="1", title="Notes",
            revision=1, updated_at="")


def _tbl(content: dict[str, Any]) -> Block:
    return Block(id="t1", note_id=1, parent_id=None, type="table",
                 props={}, content=content, text="", author="user",
                 section_key=None, changed_rev=1)


def _txt(text: str, **styles: Any) -> dict[str, Any]:
    return {"type": "text", "text": text, "styles": styles}


def _table(rows: list[list[Any]], **kw: Any) -> dict[str, Any]:
    n = len(rows[0])
    return {"type": "tableContent", "columnWidths": [None] * n,
            "headerRows": 1, "rows": [{"cells": r} for r in rows], **kw}


def _md(content: dict[str, Any]) -> str:
    return note_to_markdown(NOTE, [_tbl(content)], {})[0]


def test_basic_table() -> None:
    md = _md(_table([["指標", "值"], [[_txt("ORR", bold=True)], "62%"]]))
    assert md.endswith("| 指標 | 值 |\n| --- | --- |\n| **ORR** | 62% |\n")


def test_alignment_row_from_header_cells() -> None:
    def cell(a: str | None) -> Any:
        props = {"textAlignment": a} if a else {}
        return {"type": "tableCell", "props": props,
                "content": [_txt("h")]}
    md = _md(_table([[cell("left"), cell("center"), cell("right"), cell(None)],
                     ["a", "b", "c", "d"]]))
    assert "| :-- | :-: | --: | --- |" in md


def test_pipe_and_newline_in_a_cell() -> None:
    md = _md(_table([["h1", "h2"], ["a|b", "x\ny"]]))
    assert r"| a\|b | x y |" in md
    code = _md(_table([["h1", "h2"], [[_txt("a|b", code=True)], "z"]]))
    assert r"`a\|b`" in code


def test_no_header_row_gets_an_empty_one() -> None:
    md = _md(_table([["a", "b"], ["c", "d"]], headerRows=0))
    assert md.endswith("|  |  |\n| --- | --- |\n| a | b |\n| c | d |\n")


def test_markcite_in_a_cell_gets_its_footnote() -> None:
    from paperhub.notes.export import MarkCite
    run = {"type": "markCite", "props": {"markId": "m1", "text": "Smith"}}
    blk = _tbl(_table([["h", "h2"], [[run], "x"]]))
    cites = {"m1": MarkCite(paper_id=5, quote="the quote", section=None,
                            page=None)}
    md = note_to_markdown(NOTE, [blk], {}, mark_cites=cites)[0]
    assert "| Smith[^1] | x |" in md
    assert '[^1]: "the quote"' in md


def test_citations_inside_cells_are_found() -> None:
    run = {"type": "markCite", "props": {"markId": "m1", "text": ""}}
    papers, chunks = _cited([_tbl(_table([["see [chunk:7]", [run]]]))])
    assert chunks == {7} and papers == set()


def test_markdown_in_a_cell_stays_text() -> None:
    md = _md(_table([["h", "h2"], ["# x ![i](http://a/b.png) [l](http://a)", "|"]]))
    assert r"\!\[i\]" in md and r"\[l\]" in md


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_docx_has_a_two_row_table() -> None:
    md = _md(_table([["指標", "值"], [[_txt("ORR", bold=True)], "62%"]]))
    with zipfile.ZipFile(io.BytesIO(markdown_to_docx(md, {}))) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert xml.count("<w:tbl>") == 1
    assert len(re.findall(r"<w:tr[ >]", xml)) == 2
    assert "ORR" in xml


def _cells(md: str) -> list[list[str]]:
    """The rows of the first table, as markdown-it-py (tables on) parses it."""
    from markdown_it import MarkdownIt
    toks = MarkdownIt("commonmark").enable("table").parse(md)
    rows: list[list[str]] = []
    for t in toks:
        if t.type == "tr_open":
            rows.append([])
        elif t.type == "inline" and rows:
            rows[-1].append(t.content)
    return rows


def _math(latex: str) -> dict[str, Any]:
    return {"type": "inlineMath", "props": {"latex": latex}}


def test_math_pipes_keep_the_row_in_two_cells() -> None:
    md = _md(_table([["h", "h2"], [[_math("|x|")], [_math(r"\|v\| + a|b")]]]))
    rows = _cells(md)
    assert [len(r) for r in rows] == [2, 2]
    assert rows[1][0] == r"$\vert x\vert $"
    assert rows[1][1] == r"$\Vert v\Vert  + a\vert b$"


def test_multiline_math_stays_in_its_row() -> None:
    md = _md(_table([["h", "h2"], [[_math("a\n\nb")], "z"]]))
    assert [len(r) for r in _cells(md)] == [2, 2]


def test_code_span_with_backslash_pipe_reads_back() -> None:
    md = _md(_table([["h", "h2"], [[_txt(r"a\|b", code=True)], "z"]]))
    rows = _cells(md)
    assert [len(r) for r in rows] == [2, 2]
    # Escaped plain text (a GFM code span cannot hold backslash + pipe):
    # it renders as a\|b.
    assert rows[1][0] == "a\\\\|b"


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_docx_keeps_two_columns_with_math_pipes() -> None:
    md = _md(_table([["h", "h2"], [[_math("|x|")], "z"]]))
    with zipfile.ZipFile(io.BytesIO(markdown_to_docx(md, {}))) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert len(re.findall(r"<w:gridCol", xml)) == 2
