"""Inline code in an export stays one literal code span."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from typing import Any

import pytest

from paperhub.notes.export import note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import _READER, markdown_to_docx

needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None,
                                  reason="pandoc not installed")
NOTE = Note(id=1, target_kind="session", target_id="1", title="Notes",
            revision=1, updated_at="")
# The note.md readers: pandoc's (the .docx) and GFM-like viewers.
READERS = (_READER, "gfm+tex_math_dollars", "commonmark_x")


def _para(*runs: dict[str, Any]) -> Block:
    return Block(id="p1", note_id=1, parent_id=None, type="paragraph", props={},
                 content=list(runs), text="", author="user", section_key=None,
                 changed_rev=1)


def _code(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "styles": {"code": True}}


def _txt(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "styles": {}}


def _nodes(md: str, reader: str) -> list[dict[str, Any]]:
    """Every AST node pandoc reads from ``md``, flattened."""
    out = subprocess.run(["pandoc", "--from", reader, "--to", "json"], input=md,
                         capture_output=True, text=True, encoding="utf-8",
                         check=True).stdout
    found: list[dict[str, Any]] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            if "t" in v:
                found.append(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(json.loads(out)["blocks"])
    return found


BREAKOUTS = [
    'a`` ![br](http://127.0.0.1:9/x.png) [x]{custom-style="Title"} ``b',
    "x ``` ![y](http://127.0.0.1:9/y.png) ``` z",
    "`lead",
    "trail`",
    "` both `",
]


@needs_pandoc
@pytest.mark.parametrize("code", BREAKOUTS)
def test_inline_code_with_backticks_stays_one_code_span(code: str) -> None:
    md, _i, _b = note_to_markdown(NOTE, [_para(_txt("see "), _code(code),
                                               _txt(" after *x*"))], {})
    for reader in READERS:
        nodes = _nodes(md, reader)
        codes = [n["c"][1] for n in nodes if n["t"] == "Code"]
        assert codes == [code], (reader, md)
        kinds = {n["t"] for n in nodes}
        assert not kinds & {"Image", "Link", "Span", "Emph", "Cite"}, (reader, md)


@needs_pandoc
def test_docx_shows_inline_code_literally() -> None:
    code = BREAKOUTS[0]
    md, images, _b = note_to_markdown(NOTE, [_para(_code(code))], {})
    with zipfile.ZipFile(io.BytesIO(markdown_to_docx(md, images))) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert 'w:val="Title"' not in xml
    assert "custom-style=&quot;Title&quot;" in xml
    assert "![br](http://127.0.0.1:9/x.png)" in xml


@needs_pandoc
def test_inline_code_across_lines_is_left_alone() -> None:
    code = "first()\n    # not a heading\n\tsecond()"
    md, _i, _b = note_to_markdown(NOTE, [_para(
        _txt("before\n  indented "), _code(code), _txt("\nafter"))], {})
    span = md[md.index("first()") - 1: md.index("second()") + len("second()") + 1]
    assert "\\" not in span and "\u00a0" not in span, md
    for reader in READERS:
        nodes = _nodes(md, reader)
        codes = [n["c"][1] for n in nodes if n["t"] == "Code"]
        assert len(codes) == 1, (reader, md)
        assert "first()" in codes[0] and "second()" in codes[0]
        assert "\\" not in codes[0] and "\u00a0" not in codes[0]
        assert [n["t"] for n in nodes].count("Header") == 1, (reader, md)  # the title
        assert "CodeBlock" not in {n["t"] for n in nodes}, (reader, md)
    # The text around the code keeps its own line breaks and indent escape.
    assert "before\\\n\u00a0\u00a0indented" in md
