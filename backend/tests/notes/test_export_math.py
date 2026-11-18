"""Display math in an export stays one math node in every reader."""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from paperhub.notes.export import note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import _READER

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None,
                                reason="pandoc not installed")
NOTE = Note(id=1, target_kind="session", target_id="1", title="Notes",
            revision=1, updated_at="")
READERS = (_READER, "gfm+tex_math_dollars", "commonmark_x")
STRUCTURE = {"CodeBlock", "BlockQuote", "BulletList", "OrderedList", "Image",
             "Link", "RawBlock", "RawInline", "HorizontalRule"}

LATEX = "\n".join([
    r"a = b \\", "```", "~~~ x", "# heading", "> quote", "- item", "1. one",
    "+ plus", "    indented", r"x \\$ y", r"\$ z", "---", "![i](http://127.0.0.1:9/i.png)",
])


def _blk(bid: str, type: str, props: dict[str, Any] | None = None,
         content: Any = None) -> Block:
    return Block(id=bid, note_id=1, parent_id=None, type=type,  # type: ignore[arg-type]
                 props=props or {}, content=content, text="", author="user",
                 section_key=None, changed_rev=1)


def _blocks(md: str, reader: str) -> tuple[list[dict[str, Any]], list[str]]:
    out = subprocess.run(["pandoc", "--from", reader, "--to", "json"], input=md,
                         capture_output=True, text=True, encoding="utf-8",
                         check=True).stdout
    kinds: list[str] = []
    maths: list[dict[str, Any]] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            if "t" in v:
                kinds.append(v["t"])
                if v["t"] == "Math":
                    maths.append(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(json.loads(out)["blocks"])
    return maths, kinds


def test_multiline_math_is_one_math_node_in_every_reader() -> None:
    blocks = [
        _blk("m1", "math", {"latex": LATEX}),
        _blk("e1", "paperEquation", {"kind": "equation", "quote": "q",
                                     "pieces": [{"latex": LATEX}]}),
        _blk("p1", "paragraph", content=[{"type": "text", "text": "after",
                                          "styles": {}}]),
    ]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    for reader in READERS:
        maths, kinds = _blocks(md, reader)
        assert len(maths) == 2, (reader, md)
        for m in maths:
            assert m["c"][0]["t"] == "DisplayMath"
            assert "heading" in m["c"][1] and "i.png" in m["c"][1], (reader, m)
        assert not set(kinds) & STRUCTURE, (reader, set(kinds) & STRUCTURE, md)
        assert kinds.count("Header") == 1, (reader, md)  # the note title
        assert kinds.count("Para") == 3, (reader, md)


DOTTED = [r"x = 1 \label{eq.a}", r"y = 2 \tag{1.b}", r"a//b = c",
          r"\operatorname{e.g} x"]


@pytest.mark.parametrize("latex", DOTTED)
def test_dotted_label_and_tag_stay_verbatim_and_convert_to_word_math(latex: str) -> None:
    import io
    import zipfile

    from paperhub.notes.pandoc import markdown_to_docx
    md, _i, _b = note_to_markdown(NOTE, [_blk("m1", "math", {"latex": latex})], {})
    assert f"$${latex}$$" in md, md  # note.md keeps the LaTeX as written
    with zipfile.ZipFile(io.BytesIO(markdown_to_docx(md, {}))) as z:
        doc = z.read("word/document.xml").decode("utf-8")
    assert "<m:oMath" in doc, doc[:3000]
    # No raw TeX fallback: pandoc wrote Word math, not the source text.
    assert "\\label" not in doc and "\\tag" not in doc and "{}" not in doc, doc[:3000]
