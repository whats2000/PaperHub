"""Every user string an export writes is normalised first, so note text can
neither end a line where the exporter sees none nor forge the exporter's
own placeholders."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import pytest
from markdown_it import MarkdownIt

from paperhub.notes import export as note_export
from paperhub.notes.export import ExportPaper, _user_text, note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import _READER

needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None,
                                  reason="pandoc not installed")
# What _user_text must drop: C0/C1 controls (not \t, \n), the line and
# paragraph separators, the BOM, private use (BMP and plane 15), a lone
# surrogate and a noncharacter.
DROPPED = "\x00\x07\x0b\x0c\x1b\x7f\x85\x9f\u2028\u2029\ufeff\ue000\ue001\uf8ff" \
          "\U000f0001\U0010fffd\ud800\uffff"
_DROPPED = re.compile(f"[{re.escape(DROPPED)}\r]")


def test_user_text_normalises_line_ends_and_drops_steering_characters() -> None:
    assert _user_text("a\r\nb\rc\nd") == "a\nb\nc\nd"
    assert _user_text("x" + DROPPED + "y") == "xy"
    # Tabs, no-break and zero-width characters carry meaning: kept.
    assert _user_text("a\tb\u00a0c\u200bd\u200de\u2066f") == "a\tb\u00a0c\u200bd\u200de\u2066f"


def _probe(n: int) -> str:
    return f"Z{n}\r{DROPPED}\r\nQ{n}"


def _blk(bid: str, type: str, *, props: dict[str, Any] | None = None,
         content: Any = None, section: str | None = None) -> Block:
    return Block(id=bid, note_id=1, parent_id=None, type=type,  # type: ignore[arg-type]
                 props=props or {}, content=content, text="", author="user",
                 section_key=section, changed_rev=1)


def _txt(text: str, **styles: Any) -> dict[str, Any]:
    return {"type": "text", "text": text, "styles": styles}


def test_every_field_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    def image(self: Any, b: Block, pid: int | None) -> tuple[str, bytes] | None:
        return ((self._name(".png"), b"png")
                if b.type in ("paperFigure", "paperEquation") else None)
    monkeypatch.setattr(note_export._Writer, "image", image)
    probes = iter(range(1, 100))
    used: list[int] = []

    def p() -> str:
        n = next(probes)
        used.append(n)
        return _probe(n)

    note = Note(id=1, target_kind="paper", target_id="5", title=p(),
                revision=1, updated_at="")
    blocks = [
        _blk("s", "section", props={"title": p()}),
        _blk("h", "heading", content=[_txt(p())]),
        _blk("p", "paragraph", content=[
            _txt(p()), _txt(p(), bold=True), _txt(p(), code=True),
            {"type": "link", "href": p(), "content": [_txt(p())]},
            {"type": "mention", "props": {"label": p(), "paperId": 5}}]),
        _blk("k", "codeBlock", content=[_txt(p())]),
        _blk("m", "math", props={"latex": p()}),
        _blk("q", "paperQuote", section=p(), props={
            "paperContentId": 5, "quote": p(), "lang": "en", "orig": p()}),
        _blk("q2", "paperQuote", props={
            "paperContentId": 5, "quote": "q",
            "source": json.dumps({"section": p()})}),
        # The editor stores pieces and source as JSON strings.
        _blk("q3", "paperQuote", props={
            "paperContentId": 5, "quote": "q",
            "pieces": json.dumps([{"quote": p(), "page": 0}])}),
        _blk("f", "paperFigure", props={"paperContentId": 5, "quote": p()}),
        _blk("e", "paperEquation", props={
            "paperContentId": 5, "quote": p(),
            "pieces": json.dumps([{"latex": p()}])}),
    ]
    paper = ExportPaper(id=5, title=p(), authors=[p()], year=2017, arxiv_id=p())
    md, _images, bib = note_to_markdown(note, blocks, {5: paper})
    out = md + bib
    assert not _DROPPED.search(out), [c for c in out if _DROPPED.match(c)]
    for n in used:
        # A link target is percent-encoded: its Z/Q still show.
        assert f"Z{n}" in out and f"Q{n}" in out, n
    # The code language is not probed: it is validated, so a probe never
    # reaches the output (it is still cleaned with the other props).
    assert len(used) == 22


def _para(*runs: dict[str, Any]) -> Block:
    return _blk("p1", "paragraph", content=list(runs))


NOTE = Note(id=1, target_kind="session", target_id="1", title="Notes",
            revision=1, updated_at="")


@pytest.mark.parametrize("code", [
    "x\r\r![a](http://127.0.0.1:9/i.png)", "x\r# h", "x\r\n![a](http://127.0.0.1:9/i.png)"])
def test_carriage_return_in_inline_code_stays_one_code_span(code: str) -> None:
    md, _i, _b = note_to_markdown(NOTE, [_para(_txt("see "), _txt(code, code=True))], {})
    tokens = MarkdownIt("commonmark").parse(md)
    kinds = [t.type for t in tokens] + [c.type for t in tokens for c in t.children or []]
    assert kinds.count("code_inline") == 1, md
    assert "image" not in kinds and kinds.count("heading_open") == 1, md


def test_carriage_return_in_text_starts_no_heading() -> None:
    md, _i, _b = note_to_markdown(NOTE, [_para(_txt("x\r# h\r\n> q"))], {})
    kinds = [t.type for t in MarkdownIt("commonmark").parse(md)]
    assert kinds.count("heading_open") == 1 and "blockquote_open" not in kinds, md


def test_forged_placeholder_in_text_exports() -> None:
    md, _i, _b = note_to_markdown(NOTE, [_para(_txt("x \ue0007\ue001"))], {})
    assert md == "# Notes\n\nx 7\n"


@needs_pandoc
def test_forged_placeholder_in_math_forms_no_image() -> None:
    url = "http://127.0.0.1:9/i.png"
    blocks = [
        _para(_txt("x$$ ![a](" + url + ") $$", code=True)),
        _blk("m1", "math", props={"latex": "\ue0000\ue001"}),
        _blk("e1", "paperEquation", props={"quote": "q",
                                          "pieces": [{"latex": "\ue0000\ue001"}]}),
    ]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    for reader in (_READER, "gfm+tex_math_dollars", "commonmark_x"):
        out = subprocess.run(["pandoc", "--from", reader, "--to", "json"], input=md,
                             capture_output=True, text=True, encoding="utf-8",
                             check=True).stdout
        assert '"t":"Image"' not in json.dumps(json.loads(out), separators=(",", ":")), \
            (reader, md)
    tokens = MarkdownIt("commonmark").parse(md)
    assert not any(c.type == "image" for t in tokens for c in t.children or []), md


@needs_pandoc
def test_lone_surrogate_in_json_pieces_exports() -> None:
    from paperhub.api.notes import _zip
    from paperhub.notes.pandoc import markdown_to_docx
    bad = chr(0xD83D)
    blocks = [
        _blk("q1", "paperQuote", props={
            "quote": "q", "pieces": json.dumps([{"quote": f"a{bad} b\r# h\r- x"}]),
            "source": json.dumps({"section": f"S{bad}"})}),
        _blk("e1", "paperEquation", props={
            "quote": "c", "pieces": json.dumps([{"latex": f"x{bad}+1"}])}),
    ]
    md, images, bib = note_to_markdown(NOTE, blocks, {})
    assert bad not in md and "\r" not in md
    assert "a b" in md and "x+1" in md
    _zip(md, images, bib)
    markdown_to_docx(md, images)
    kinds = [t.type for t in MarkdownIt("commonmark").parse(md)]
    assert kinds.count("heading_open") == 1 and "bullet_list_open" not in kinds, md
