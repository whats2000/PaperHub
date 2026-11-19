"""Exporting a note as Markdown (with images and BibTeX) and as Word."""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from paperhub.notes.export import ExportPaper, note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import markdown_to_docx
from paperhub.pipelines.paper_asset import FigureAsset, PaperAsset, write_paper_asset

NOTE = Note(id=1, target_kind="paper", target_id="5", title="Attention notes",
            revision=3, updated_at="")


def _blk(bid: str, type: str, *, props: dict[str, Any] | None = None,
         content: Any = None, parent: str | None = None,
         section: str | None = None) -> Block:
    return Block(id=bid, note_id=1, parent_id=parent, type=type,  # type: ignore[arg-type]
                 props=props or {}, content=content, text="", author="user",
                 section_key=section, changed_rev=1)


def _txt(text: str, **styles: Any) -> dict[str, Any]:
    return {"type": "text", "text": text, "styles": styles}


def _paper(tmp: Path | None = None, pid: int = 5,
           title: str = "Attention Is All You Need") -> ExportPaper:
    return ExportPaper(id=pid, title=title, authors=["Ashish Vaswani", "Noam Shazeer"],
                       year=2017, arxiv_id="1706.03762", source_dir=tmp)


def _quote(bid: str, quote: str, *, page: int | None = 2,
           section: str = "3 Model", **extra: Any) -> Block:
    source: dict[str, Any] = {"section": section}
    if page is not None:
        source["page"] = page
    return _blk(bid, "paperQuote", section=section, props={
        "markId": f"mk_{bid}", "kind": "quote", "color": "green", "quote": quote,
        "pieces": [{"quote": quote, "page": page, "rects": [[0.1, 0.1, 0.5, 0.2]]}],
        "source": source, **extra})


def _paper_dir(tmp: Path) -> Path:
    """A cache dir with a two-page PDF and one figure image."""
    src = tmp / "cache"
    src.mkdir()
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    for _ in range(2):
        page = doc.new_page()  # type: ignore[no-untyped-call]
        page.insert_text((72, 72), "Scaled dot-product attention")  # type: ignore[no-untyped-call]
    doc.save(src / "paper.pdf")  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]
    asset = PaperAsset(figures=[FigureAsset(
        id="fig-001", caption="The Transformer", page=2, section="3 Model",
        image_path="figures/fig-001.png")])
    write_paper_asset(asset, src)
    (src / "asset" / "figures").mkdir(parents=True, exist_ok=True)
    (src / "asset" / "figures" / "fig-001.png").write_bytes(b"\x89PNG-figure")
    return src


def test_markdown_has_sections_quotes_and_sources() -> None:
    blocks = [
        _blk("s1", "section", props={"title": "3 Model"}, section="3 Model"),
        _quote("q1", "An attention function maps a query to an output."),
        _blk("p1", "paragraph", content=[_txt("Key idea "), _txt("here", bold=True)]),
        _blk("h1", "heading", props={"level": 1}, content=[_txt("My take")]),
        _blk("m1", "math", props={"latex": "QK^T"}),
    ]
    md, _images, bib = note_to_markdown(NOTE, blocks, {5: _paper()})
    assert md.startswith("# Attention notes\n")
    assert "\n## 3 Model\n" in md
    assert "> An attention function maps a query to an output." in md
    assert "> — Attention Is All You Need, §3, p. 3 [@vaswani2017attention]" in md
    assert "Key idea **here**" in md
    assert "\n### My take\n" in md
    assert "$$QK^T$$" in md
    assert "@misc{vaswani2017attention," in bib


def test_note_without_sections_has_no_section_headings() -> None:
    blocks = [_quote("q1", "An attention function."),
              _blk("h1", "heading", props={"level": 1}, content=[_txt("My take")])]
    md, _images, _bib = note_to_markdown(NOTE, blocks, {5: _paper()})
    assert "\n## My take\n" in md  # no +2 section offset
    assert "## 3 Model" not in md


def test_joined_quote_reads_in_reading_order_and_translation_is_labelled() -> None:
    pieces = [
        {"quote": "second", "page": 3, "rects": [[0.1, 0.5, 0.2, 0.6]], "stroke": 1},
        {"quote": "first", "page": 3, "rects": [[0.1, 0.1, 0.2, 0.2]], "stroke": 0},
    ]
    q = _blk("q1", "paperQuote", props={
        "kind": "quote", "quote": "x", "pieces": json.dumps(pieces),
        "source": json.dumps({"page": 3}), "lang": "繁體中文",
        "orig": "The original words."})
    md, _i, _b = note_to_markdown(NOTE, [q], {5: _paper()})
    assert "> first … second" in md
    assert "> *Translated · 繁中*" in md
    assert "> Original: The original words." in md


def test_bib_has_one_entry_per_quoted_paper() -> None:
    other = _paper(pid=9, title="The BERT Paper")
    session = Note(id=2, target_kind="session", target_id="1", title="Chat",
                   revision=1, updated_at="")
    blocks = [
        _blk("p1", "paragraph", content=[
            _txt("Attention works "), _txt("[chunk:11, 12]"), _txt(" and "),
            {"type": "mention", "props": {"kind": "paper", "paperId": 9,
                                          "label": "BERT"}},
            _txt(" agrees [fig:9/fig-002] [view:9/4/1,2,300,400]."),
        ]),
        _blk("p2", "paragraph", content=[_txt("Again [chunk:12] [note:b_abc].")]),
    ]
    md, _i, bib = note_to_markdown(session, blocks, {5: _paper(), 9: other},
                                   chunk_papers={11: 5, 12: 5})
    assert bib.count("@misc{") == 2
    assert "@misc{vaswani2017attention," in bib and "@misc{vaswani2017bert," in bib
    assert "Attention works [@vaswani2017attention]" in md
    assert "BERT [@vaswani2017bert]" in md
    assert "[@vaswani2017bert, p. 4]" in md
    assert "[chunk:" not in md and "[note:" not in md


def test_images_written_for_figures_and_boxes(tmp_path: Path) -> None:
    src = _paper_dir(tmp_path)
    blocks = [
        _blk("f1", "paperFigure", props={
            "kind": "figure", "quote": "The Transformer",
            "pieces": [{"figure_id": "fig-001"}], "source": {"section": "3 Model"}}),
        _blk("r1", "paperRegion", props={
            "kind": "region", "quote": "Table 2 numbers",
            "pieces": [{"page": 1, "box": [0.1, 0.1, 0.6, 0.3]}],
            "source": {"page": 1}}),
        _blk("e1", "paperEquation", props={
            "kind": "equation", "quote": "softmax",
            "pieces": [{"page": 0, "box": [0.1, 0.05, 0.9, 0.2],
                        "latex": r"\mathrm{softmax}(x)"}],
            "source": {"page": 0}}),
    ]
    md, images, _bib = note_to_markdown(NOTE, blocks, {5: _paper(src)})
    assert images["images/0001.png"] == b"\x89PNG-figure"
    assert images["images/0002.png"].startswith(b"\x89PNG")
    assert images["images/0003.png"].startswith(b"\x89PNG")
    assert "![The Transformer](images/0001.png)" in md
    assert "![Table 2 numbers](images/0002.png)" in md
    assert r"$$\mathrm{softmax}(x)$$" in md


def test_missing_image_keeps_caption() -> None:
    fig = _blk("f1", "paperFigure", props={
        "kind": "figure", "quote": "The Transformer",
        "pieces": [{"page": 1, "box": [0.1, 0.1, 0.5, 0.5]}], "source": {"page": 1}})
    md, images, _bib = note_to_markdown(NOTE, [fig], {5: _paper(Path("nowhere"))})
    assert images == {}
    assert "The Transformer\n\n[image unavailable]" in md


def test_divider_exports_as_rule() -> None:
    blocks = [
        _blk("p1", "paragraph", content=[_txt("one")]),
        _blk("d1", "divider"),
        _blk("p2", "paragraph", content=[_txt("two")]),
    ]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    assert "one\n\n---\n\ntwo" in md


def test_hidden_and_commented_text_export_as_plain_text() -> None:
    blocks = [_blk("p1", "paragraph", content=[
        _txt("Shown "), _txt("secret", hidden=True), _txt(" and "),
        _txt("discussed", comment="cm_abc123"), _txt(" text.")])]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    assert "Shown secret and discussed text." in md
    assert "cm_abc123" not in md and "||" not in md


def test_lists_code_and_checks() -> None:
    blocks = [
        _blk("l1", "bulletListItem", content=[_txt("top")]),
        _blk("l2", "bulletListItem", content=[_txt("child")], parent="l1"),
        _blk("c1", "checkListItem", props={"checked": True}, content=[_txt("done")]),
        _blk("n1", "numberedListItem", content=[_txt("step")]),
        _blk("k1", "codeBlock", props={"language": "python"},
             content=[_txt("print('*x*')")]),
        _blk("p1", "paragraph", content=[_txt("# not a heading with *stars*")]),
    ]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    assert "- top\n    - child\n- [x] done\n\n1. step" in md
    assert "```python\nprint('*x*')\n```" in md
    assert r"\# not a heading with \*stars\*" in md


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_docx_roundtrip_when_pandoc_present(tmp_path: Path) -> None:
    src = _paper_dir(tmp_path)
    blocks = [
        _blk("s1", "section", props={"title": "3 Model"}, section="3 Model"),
        _quote("q1", "An attention function maps a query to an output."),
        _blk("f1", "paperFigure", props={
            "kind": "figure", "quote": "The Transformer",
            "pieces": [{"page": 1, "box": [0.1, 0.05, 0.9, 0.3]}],
            "source": {"page": 1}}),
        _blk("d1", "divider"),
        _blk("m1", "math", props={"latex": "a^2+b^2"}),
    ]
    md, images, bib = note_to_markdown(NOTE, blocks, {5: _paper(src)})
    docx = markdown_to_docx(md, {**images, "references.bib": bib.encode()})
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    assert "An attention function maps a query to an output." in xml
    assert "3 Model" in xml
    assert "Vaswani" in xml  # the citation, rendered by citeproc
    assert "<m:oMath" in xml  # the equation is Word math
    assert len(media) == 1


BS = "\\"


def _quote_md(quote: str) -> str:
    md, _i, _b = note_to_markdown(NOTE, [_quote("q1", quote)], {5: _paper()})
    return md


def test_quote_inline_math_stays_math_and_text_stays_escaped() -> None:
    md = _quote_md("with x_i and $x_i^2$ here")
    assert f"> with x{BS}_i and $x_i^2$ here" in md


def test_quote_aligned_environment_is_display_math_without_label() -> None:
    md = _quote_md(r"see $\begin{aligned} a &= b \label{eq:a} \end{aligned}$ ok")
    assert "label" not in md
    assert (f"> see\n>\n> $${BS}begin{{aligned}} a &= b {BS}end{{aligned}}$$"
            "\n>\n> ok") in md


def test_quote_escaped_dollar_is_a_literal() -> None:
    md = _quote_md(r"costs \$5 and \$6")
    assert f"> costs {BS * 3}$5 and {BS * 3}$6" in md
    assert "$$" not in md


def test_quote_without_math_is_unchanged() -> None:
    md = _quote_md("plain a_b\n1. next [x]")
    assert f"> plain a{BS}_b\n>\n> 1{BS}. next {BS}[x{BS}]" in md


def _imath(latex: str) -> dict[str, Any]:
    return {"type": "inlineMath", "props": {"latex": latex}}


def test_inline_math_exports_as_dollar_math() -> None:
    md, _i, _b = note_to_markdown(NOTE, [_blk("p1", "paragraph", content=[
        _txt("energy "), _imath(r"E = mc^2"), _txt(" and $5")])], {5: _paper()})
    assert f"energy $E = mc^2$ and {BS}$5" in md


def test_inline_math_cannot_close_early_and_empty_is_dropped() -> None:
    md, _i, _b = note_to_markdown(NOTE, [_blk("p1", "paragraph", content=[
        _txt("a "), _imath(" x$y "), _imath("  "), _txt("b")])], {5: _paper()})
    assert f"a $x{BS}$y$b" in md
