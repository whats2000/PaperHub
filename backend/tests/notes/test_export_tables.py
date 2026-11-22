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
    # Left is the default: one spelling, the same as gfm_table writes.
    assert "| --- | :-: | --: | --- |" in md
    assert ":--" not in md


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
    assert rows[1][0] == r"$\vert{}x\vert{}$"
    assert rows[1][1] == r"$\Vert{}v\Vert{} + a\vert{}b$"


def test_math_pipes_in_text_and_urls() -> None:
    md = _md(_table([["h", "h2"], [
        [_math(r"\text{a|b} |x|")], [_math(r"\href{http://a.b/c}{u} http://x.y")]]]))
    rows = _cells(md)
    assert [len(r) for r in rows] == [2, 2]
    assert rows[1][0] == r"$\text{a}\vert{}\text{b} \vert{}x\vert{}$"
    assert rows[1][1] == r"$\href{http://a.b/c}{u} http:/{}/x.y$"


def test_pipe_in_other_text_commands_and_nested_braces() -> None:
    md = _md(_table([["h", "h2"], [
        [_math(r"\textbf{a|b}")], [_math(r"\text{a{b|c}d} \\ |")]]]))
    rows = _cells(md)
    assert [len(r) for r in rows] == [2, 2]
    assert rows[1][0] == r"$\textbf{a}\vert{}\textbf{b}$"
    assert rows[1][1] == r"$\text{a{b}}\vert{}\text{{c}d} \\ \vert{}$"


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_pandoc_reads_text_mode_pipe_as_math_without_warning() -> None:
    import subprocess

    from paperhub.notes.pandoc import _READER
    md = _md(_table([["h", "h2"], [[_math(r"\text{a|b}")], "z"]]))
    out = subprocess.run(["pandoc", "--from", _READER, "--to", "native"],
                         input=md, capture_output=True, text=True,
                         encoding="utf-8", check=True)
    assert out.stdout.count("Math InlineMath") == 1
    assert "Could not convert" not in out.stderr
    docx = subprocess.run(["pandoc", "--from", _READER, "--to", "docx",
                           "--output", "-"], input=md.encode("utf-8"),
                          capture_output=True, check=True)
    assert b"Could not convert" not in docx.stderr


def test_code_span_with_backslash_pipe_keeps_its_spaces() -> None:
    md = _md(_table([["h", "h2"], [[_txt(r" a  \|b ", code=True)], "z"]]))
    assert "| a  \\\\\\|b | z |" in md


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_pandoc_reads_cell_math_with_pipes_as_math() -> None:
    import subprocess

    from paperhub.notes.pandoc import _READER
    md = _md(_table([["h", "h2"], [[_math("|x|")], [_math(r"\|v\|")]]]))
    out = subprocess.run(["pandoc", "--from", _READER, "--to", "native"],
                         input=md, capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    assert out.count("Math InlineMath") == 2


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


# Table style (three-line / grid): the .docx draws every table in the
# viewer's style; the Markdown is the same plain pipe table either way.

def _docx_tables(style: str | None) -> str:
    md = _md(_table([["指標", "值"], ["ORR", "62%"], ["PFS", "8.1"]]))
    kw = {} if style is None else {"table_style": style}
    with zipfile.ZipFile(io.BytesIO(markdown_to_docx(md, {}, **kw))) as z:
        return z.read("word/document.xml").decode("utf-8")


def _border(xml: str, side: str) -> str | None:
    m = re.search(rf'<w:{side} w:val="(\w+)"', xml)
    return m[1] if m else None


def _rows(xml: str) -> list[str]:
    return re.findall(r"<w:tr[ >].*?</w:tr>", xml, re.S)


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
@pytest.mark.parametrize("style", [None, "threeLine"])
def test_three_line_docx_table(style: str | None) -> None:
    xml = _docx_tables(style)
    tbl = re.search(r"<w:tblBorders>(.*?)</w:tblBorders>", xml, re.S)
    assert tbl, "the table states its own borders"
    b = tbl[1]
    # A thick rule above and at the end; no vertical, no inner horizontal.
    assert _border(b, "top") == "single" and _border(b, "bottom") == "single"
    for side in ("left", "right", "insideH", "insideV"):
        assert _border(b, side) == "nil", side
    rows = _rows(xml)
    assert len(rows) == 3
    # A thin rule under the header row only; the body rows draw none.
    head = re.findall(r"<w:tcBorders>(.*?)</w:tcBorders>", rows[0], re.S)
    assert len(head) == 2 and all(_border(h, "bottom") == "single" for h in head)
    top_sz = int(re.search(r'<w:top w:val="single" w:sz="(\d+)"', b)[1])  # type: ignore[index]
    mid_sz = int(re.search(r'<w:bottom w:val="single" w:sz="(\d+)"', head[0])[1])  # type: ignore[index]
    assert top_sz > mid_sz
    for row in rows[1:]:
        assert "<w:tcBorders>" not in row and "<w:b " not in row
    assert rows[0].count("<w:b />") == 2  # the header bold, as in the editor
    assert "<w:shd " not in xml  # no header tint


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_grid_docx_table() -> None:
    xml = _docx_tables("grid")
    tbl = re.search(r"<w:tblBorders>(.*?)</w:tblBorders>", xml, re.S)
    assert tbl
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        assert _border(tbl[1], side) == "single", side
    rows = _rows(xml)
    # The header row is tinted; the body is not.
    assert rows[0].count("<w:shd ") == 2
    assert all("<w:shd " not in r for r in rows[1:])


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_styled_docx_keeps_schema_order_and_text() -> None:
    for style in ("threeLine", "grid"):
        xml = _docx_tables(style)
        assert "ORR" in xml and "指標" in xml
        tblpr = re.search(r"<w:tblPr>(.*?)</w:tblPr>", xml, re.S)[1]  # type: ignore[index]
        names = re.findall(r"<w:(\w+)", tblpr)
        top = [n for n in names if n in ("tblStyle", "tblW", "jc", "tblBorders", "tblLook")]
        assert top == ["tblStyle", "tblW", "jc", "tblBorders", "tblLook"], top


def test_style_tables_rejects_an_unknown_style() -> None:
    from paperhub.notes.pandoc import PandocError, style_docx_tables
    with pytest.raises(PandocError):
        style_docx_tables(b"", "dotted")  # type: ignore[arg-type]


def test_markdown_is_the_same_in_either_style() -> None:
    # The style is the viewer's, not the note's: no switch in note_to_markdown.
    import inspect
    assert "table_style" not in inspect.signature(note_to_markdown).parameters


# style_docx_tables on hand-built documents (no pandoc needed).

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _row(cells: list[str], header: bool = False, rpr: str = "") -> str:
    trpr = '<w:trPr><w:tblHeader w:val="true" /></w:trPr>' if header else ""
    tcs = "".join(f"<w:tc><w:tcPr /><w:p><w:r>{rpr}<w:t>{c}</w:t></w:r></w:p></w:tc>"
                  for c in cells)
    return f"<w:tr>{trpr}{tcs}</w:tr>"


def _tbl_xml(rows: str, tblpr: str = '<w:tblStyle w:val="Table" />') -> str:
    return f"<w:tbl><w:tblPr>{tblpr}</w:tblPr><w:tblGrid /> {rows}</w:tbl>"


def _docx_of(body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml",
                   f'<?xml version="1.0"?><w:document {_W}><w:body>{body}'
                   "</w:body></w:document>")
        z.writestr("word/styles.xml", "<w:styles/>")
    return buf.getvalue()


def _doc_xml(docx: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_style_two_tables_in_one_document() -> None:
    from paperhub.notes.pandoc import style_docx_tables
    t = _tbl_xml(_row(["h1", "h2"], header=True) + _row(["a", "b"]))
    out = _doc_xml(style_docx_tables(_docx_of(t + "<w:p />" + t), "threeLine"))
    tables = re.findall(r"<w:tbl>.*?</w:tbl>", out, re.S)
    assert len(tables) == 2
    for tbl in tables:
        assert tbl.count("<w:tblBorders>") == 1
        assert _rows(tbl)[0].count("<w:tcBorders>") == 2


def test_style_keeps_other_parts_and_order() -> None:
    from paperhub.notes.pandoc import style_docx_tables
    src = _docx_of(_tbl_xml(_row(["h"], header=True) + _row(["a"])))
    out = style_docx_tables(src, "grid")
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        assert z.namelist() == ["[Content_Types].xml", "word/document.xml",
                                "word/styles.xml"]
        assert z.read("word/styles.xml") == b"<w:styles/>"


def test_style_document_without_a_table_is_unchanged() -> None:
    from paperhub.notes.pandoc import style_docx_tables
    src = _docx_of("<w:p><w:r><w:t>no table</w:t></w:r></w:p>")
    assert style_docx_tables(src, "threeLine") == src
    assert style_docx_tables(src, "grid") == src


def test_style_non_zip_is_a_pandoc_error() -> None:
    from paperhub.notes.pandoc import PandocError, style_docx_tables
    with pytest.raises(PandocError):
        style_docx_tables(b"not a zip", "threeLine")


@pytest.mark.parametrize("style", ["threeLine", "grid"])
def test_header_text_is_bold(style: str) -> None:
    from paperhub.notes.pandoc import style_docx_tables
    t = _tbl_xml(_row(["h1", "h2"], header=True)
                 + _row(["a", "b"]))
    out = _doc_xml(style_docx_tables(_docx_of(t), style))  # type: ignore[arg-type]
    head, body = _rows(out)
    assert head.count("<w:b />") == 2
    assert "<w:b " not in body


def test_header_bold_keeps_run_property_order() -> None:
    from paperhub.notes.pandoc import style_docx_tables
    # A code run (rStyle) and an already bold run: b after rStyle, once.
    t = _tbl_xml(
        "<w:tr><w:trPr><w:tblHeader w:val=\"true\" /></w:trPr>"
        "<w:tc><w:tcPr /><w:p><w:r><w:rPr><w:rStyle w:val=\"VerbatimChar\" />"
        "</w:rPr><w:t>x</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:tcPr /><w:p><w:r><w:rPr><w:b /><w:i /></w:rPr><w:t>y</w:t>"
        "</w:r></w:p></w:tc></w:tr>" + _row(["a", "b"]))
    head = _rows(_doc_xml(style_docx_tables(_docx_of(t), "threeLine")))[0]
    assert '<w:rPr><w:rStyle w:val="VerbatimChar" /><w:b /></w:rPr>' in head
    assert "<w:rPr><w:b /><w:i /></w:rPr>" in head


def test_first_row_is_the_header_without_tblheader() -> None:
    """pandoc 2.15-2.19 may not mark the header row: the first row is it
    (every exported pipe table has one)."""
    from paperhub.notes.pandoc import style_docx_tables
    t = _tbl_xml(_row(["h1", "h2"]) + _row(["a", "b"]))
    head, body = _rows(_doc_xml(style_docx_tables(_docx_of(t), "threeLine")))
    assert head.count("<w:tcBorders>") == 2 and head.count("<w:b />") == 2
    assert "<w:tcBorders>" not in body and "<w:b " not in body


def test_unparsed_properties_still_get_one_child_of_a_name() -> None:
    """The fallback (properties this cannot parse into children) replaces
    an existing child of the same name instead of adding a second."""
    from paperhub.notes.pandoc import _TBLPR_ORDER, _with_child
    inner = ('<w:tblStyle w:val="Table" />text?<w:tblBorders><w:top w:val="dotted" />'
             "</w:tblBorders>")
    out = _with_child(inner, "<w:tblBorders><w:top w:val=\"single\" /></w:tblBorders>",
                      _TBLPR_ORDER)
    assert out.count("<w:tblBorders>") == 1
    assert 'w:val="single"' in out and "dotted" not in out
    shd = _with_child('<w:shd w:fill="000000" />junk', '<w:shd w:fill="F2F2F2" />', _TBLPR_ORDER)
    assert shd.count("<w:shd ") == 1 and "F2F2F2" in shd


def test_malformed_properties_are_a_pandoc_error() -> None:
    from paperhub.notes.pandoc import PandocError, _props
    with pytest.raises(PandocError):
        _props("no element here", "tblPr", "<w:x />", ("x",))


# ------------------------------------------------- caption and notes (Task 13)

def _ctbl(bid: str, props: dict[str, Any], rows: list[list[Any]] | None = None) -> Block:
    return Block(id=bid, note_id=1, parent_id=None, type="table", props=props,
                 content=_table(rows or [["k", "v"], ["ORR", "62%"]]),
                 text="", author="user", section_key=None, changed_rev=1)


def _para(bid: str, text: str) -> Block:
    return Block(id=bid, note_id=1, parent_id=None, type="paragraph", props={},
                 content=[_txt(text)], text=text, author="user",
                 section_key=None, changed_rev=1)


CAP = {"caption": "Response rates $p<0.05$",
       "notes": "ORR, objective response.\n* $p<0.05$"}


def test_markdown_caption_line_and_notes_paragraph() -> None:
    md = note_to_markdown(NOTE, [_ctbl("t1", CAP)], {})[0]
    # The math as a quote's ("<" kept from reading as HTML), one paragraph
    # of notes with a hard break per line.
    assert md.endswith(
        "| ORR | 62% |\n\nTable: Response rates $p<{}0.05$\n\n"
        "ORR, objective response.\\\n\\* $p<{}0.05$\n")


def test_markdown_caption_only_and_notes_only() -> None:
    md = note_to_markdown(NOTE, [_ctbl("t1", {"caption": "Only"})], {})[0]
    assert md.endswith("| ORR | 62% |\n\nTable: Only\n")
    md = note_to_markdown(NOTE, [_ctbl("t1", {"notes": "n.s."})], {})[0]
    assert md.endswith("| ORR | 62% |\n\nn\\.s.\n")
    assert "Table:" not in md


def test_a_paragraph_that_reads_as_a_caption_is_escaped() -> None:
    md = note_to_markdown(NOTE, [_ctbl("t1", {}), _para("p1", "Table: mine"),
                                 _para("p2", ": also mine")], {})[0]
    assert "\n\nTable\: mine\n\n\: also mine\n" in md


def test_table_notes_flags_follow_the_written_tables() -> None:
    broken = Block(id="t0", note_id=1, parent_id=None, type="table", props={"notes": "x"},
                   content={"type": "tableContent", "rows": []}, text="",
                   author="user", section_key=None, changed_rev=1)
    blocks = [_ctbl("t1", CAP), broken, _ctbl("t2", {"caption": "c"}),
              _ctbl("t3", {"notes": "  \n\x00"}), _ctbl("t4", {"notes": "y"})]
    flags: list[bool] = []
    note_to_markdown(NOTE, blocks, {}, table_notes=flags)
    assert flags == [True, False, False, True]


def _docx_parts(blocks: list[Block], style: str = "threeLine",
                label: str = "Table") -> tuple[str, str]:
    flags: list[bool] = []
    md = note_to_markdown(NOTE, blocks, {}, table_notes=flags)[0]
    docx = markdown_to_docx(md, {}, table_style=style,  # type: ignore[arg-type]
                            table_label=label, table_notes=flags)
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        return (z.read("word/document.xml").decode("utf-8"),
                z.read("word/styles.xml").decode("utf-8"))


def _caption_paras(xml: str) -> list[str]:
    return re.findall(r'<w:p><w:pPr><w:pStyle w:val="TableCaption" />.*?</w:p>', xml, re.S)


def _text_of(p: str) -> str:
    return "".join(re.findall(r"<(?:w|m):t(?: [^>]*)?>([^<]*)</(?:w|m):t>", p))


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
@pytest.mark.parametrize("style", ["threeLine", "grid"])
def test_docx_numbered_caption_and_small_notes(style: str) -> None:
    blocks = [_ctbl("t1", CAP), _para("p1", "Between"),
              _ctbl("t2", {"caption": "Second"}), _para("p2", "After")]
    xml, styles = _docx_parts(blocks, style)
    caps = _caption_paras(xml)
    assert len(caps) == 2
    # A real Word caption: "Table" and a SEQ field, bold, before the text.
    assert all(r'w:instr=" SEQ Table \* ARABIC "' in c for c in caps)
    assert _text_of(caps[0]).startswith("Table 1. Response rates")
    assert _text_of(caps[1]) == "Table 2. Second"
    # The notes: one paragraph in the small Table Note style, after table 1.
    notes = re.findall(r'<w:p><w:pPr><w:pStyle w:val="TableNote" />.*?</w:p>', xml, re.S)
    assert len(notes) == 1 and "objective response" in notes[0]
    assert xml.index(notes[0]) > xml.index("</w:tbl>")
    style_el = re.search(r'<w:style [^>]*w:styleId="TableNote".*?</w:style>', styles, re.S)
    assert style_el and '<w:sz w:val="18" />' in style_el[0]
    # The other paragraphs stay body text; the tables keep their style.
    assert "TableNote" not in re.search(r"<w:p>(?:(?!</w:p>).)*Between.*?</w:p>", xml, re.S)[0]  # type: ignore[index]
    assert "TableNote" not in re.search(r"<w:p>(?:(?!</w:p>).)*After.*?</w:p>", xml, re.S)[0]  # type: ignore[index]
    assert xml.count("<w:tblBorders>") == 2


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_docx_caption_label_is_the_viewers() -> None:
    xml, _ = _docx_parts([_ctbl("t1", {"caption": "反應率"})], label="表")
    assert _text_of(_caption_paras(xml)[0]) == "表 1. 反應率"


def test_docx_caption_label_is_escaped() -> None:
    from paperhub.notes.pandoc import docx_table_captions
    body = ('<w:p><w:pPr><w:pStyle w:val="TableCaption" /></w:pPr>'
            '<w:r><w:t xml:space="preserve">c</w:t></w:r></w:p>')
    out = docx_table_captions(_docx_of(body), "<b>&")
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "&lt;b&gt;&amp; " in xml and "<b>" not in xml
