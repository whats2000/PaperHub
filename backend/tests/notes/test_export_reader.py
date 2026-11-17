"""The Word export's Markdown reader keeps typography and heading anchors."""
from __future__ import annotations

import io
import re
import shutil
import zipfile
from typing import Any

import pytest

from paperhub.notes.export import ExportPaper, note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import markdown_to_docx

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None,
                                reason="pandoc not installed")

NOTE = Note(id=1, target_kind="session", target_id="1", title="Notes",
            revision=1, updated_at="")


def _blk(bid: str, type: str, content: Any = None,
         props: dict[str, Any] | None = None) -> Block:
    return Block(id=bid, note_id=1, parent_id=None, type=type,  # type: ignore[arg-type]
                 props=props or {}, content=content, text="", author="user",
                 section_key=None, changed_rev=1)


def test_docx_has_curly_quotes_and_heading_bookmarks() -> None:
    blocks = [
        _blk("h1", "heading", [{"type": "text", "text": "First part", "styles": {}}],
             {"level": 1}),
        _blk("p1", "paragraph", [{"type": "text", "styles": {},
                                  "text": '"ResNet" is it\'s best -- see pages 1--3'}]),
        _blk("h2", "heading", [{"type": "text", "text": "Second part", "styles": {}}],
             {"level": 2}),
    ]
    md, images, _bib = note_to_markdown(NOTE, blocks, {})
    with zipfile.ZipFile(io.BytesIO(markdown_to_docx(md, images))) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    assert "“ResNet”" in text and "it’s" in text
    assert "–" in text  # -- is an en dash
    # The note title and both headings carry bookmarks (heading anchors).
    assert xml.count("<w:bookmarkStart") >= 3
    assert 'w:name="h-first-part"' in xml


def test_heading_titled_refs_keeps_off_the_bibliography_bookmark() -> None:
    paper = ExportPaper(id=7, title="Deep Residual Learning", authors=["Kaiming He"],
                        year=2016)
    blocks = [
        _blk("h1", "heading", [{"type": "text", "text": "refs", "styles": {}}],
             {"level": 1}),
        _blk("h2", "heading", [{"type": "text", "styles": {},
                                "text": "ref he2016deep"}], {"level": 1}),
        _blk("p1", "paragraph", [{"type": "text", "text": "See [fig:7/f1].",
                                  "styles": {}}]),
    ]
    md, images, bib = note_to_markdown(
        Note(id=1, target_kind="session", target_id="1", title="refs",
             revision=1, updated_at=""), blocks, {7: paper})
    docx = markdown_to_docx(md, {**images, "references.bib": bib.encode()})
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    names = re.findall(r'<w:bookmarkStart[^>]*w:name="([^"]+)"', xml)
    assert names.count("refs") == 1 and names.count("ref-he2016deep") == 1, names
    assert len(names) == len(set(names)), names
