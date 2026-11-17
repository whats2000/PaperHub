"""The reader's text rule, server side (``notes/html_text.py``).

``frontend/src/lib/__fixtures__/mark_text/rule.*`` is shared with the
frontend (``frontend/src/lib/marks/anchor.test.ts``): both sides must read the same
text from the same HTML, or a quote made in the reader is not found again.
"""
import json
from pathlib import Path

from paperhub.notes.html_text import build_text_index, paragraph_at, tex_text

FIXTURE = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib"
           / "__fixtures__" / "mark_text")


def test_shared_fixture_reads_as_the_reader_does() -> None:
    html = (FIXTURE / "rule.html").read_text(encoding="utf-8")
    want = json.loads((FIXTURE / "rule.expected.json").read_text(encoding="utf-8"))
    got = build_text_index(html)
    assert got.text == want["text"]
    assert [list(s) for s in got.spans] == want["spans"]


def test_math_reads_as_its_tex() -> None:
    assert tex_text("\(h_{t}\)") == "$h_{t}$"
    assert tex_text("\[\n  a +  b\n\]") == "$a + b$"
    assert tex_text("  ") == ""
    idx = build_text_index(
        '<p>x <span class="math inline"><mjx-container><math><semantics><mi>h</mi>'
        '<annotation encoding="application/x-tex">h_t</annotation></semantics>'
        "</math></mjx-container></span> y</p>")
    assert idx.text == "x $h_t$ y"


def test_blocks_and_skipped_parts() -> None:
    idx = build_text_index(
        "<body><p>one</p><p>two</p><div class='ph-tr'>nope</div>"
        "<script>nope</script><p>th<b>ree</b></p></body>")
    assert idx.text == "one two three"


def test_implied_end_tags_close_as_a_browser_does() -> None:
    from paperhub.notes.html_text import _Builder

    def open_tags(html: str) -> list[str]:
        b = _Builder()
        b.feed(html)
        return [e.tag for e in b.stack]

    # A block start closes an open <p>; a <li> closes the open item, not
    # the item of an outer list; a table cell is a scope boundary.
    assert open_tags("<body><p>a<div>b") == ["body", "div"]
    assert open_tags("<ul><li>a<li>b") == ["ul", "li"]
    assert open_tags("<ul><li>a<ul><li>b") == ["ul", "li", "ul", "li"]
    assert open_tags("<dl><dt>a<dd>b") == ["dl", "dd"]
    assert open_tags("<p>a<table><tr><td><div>x") == ["table", "tr", "td", "div"]
    assert open_tags("<p><span>a<div>b") == ["div"]


def test_text_directly_under_body_is_in_no_paragraph() -> None:
    """Loose text under <body> (the implicit body, id -1) is not one
    paragraph: two unrelated passages there must never join."""
    index = build_text_index("<html><body>Loose words here.<p>A real paragraph.</p>More loose text.</body></html>")
    loose = index.text.index("Loose")
    later = index.text.index("More loose")
    inside = index.text.index("real")
    assert paragraph_at(index, loose) is None
    assert paragraph_at(index, later) is None
    assert isinstance(paragraph_at(index, inside), int)

