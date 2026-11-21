"""Property test of the note export: whatever a note holds, note.md renders
only the images, links and headings the exporter wrote, and no raw HTML;
the Word export fetches no URL and reads no local file.

Every payload from the export reviews (and a seeded mix of them) goes into
every user field of one note. note.md is then rendered with markdown-it
(CommonMark, the reader that keeps ``\\r``) and parsed with pandoc's
GFM, CommonMark-x and export readers.
"""
from __future__ import annotations

import http.server
import io
import json
import random
import re
import shutil
import subprocess
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

from paperhub.notes import export as note_export
from paperhub.notes.export import ExportPaper, note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import _READER, markdown_to_docx

needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None,
                                  reason="pandoc not installed")
PANDOC_READERS = (_READER, "gfm+tex_math_dollars", "commonmark_x")
# The exporter's own constructs in the fixture note.
HEADINGS = 3            # the note title, a section, a heading block
LINKS = 2               # the link run, and the one in a table cell
CODE_BLOCKS = 1         # the one code block
OWN_IMAGES = {"images/0001.png", "images/0002.png"}  # figure + equation
SECRET = "LOCAL-SECRET-7d1f"


def _fragments(port: int, secret: Path) -> list[str]:
    """Every construct the reviews used to break out of the export."""
    url = f"http://127.0.0.1:{port}/i.png"
    path = secret.as_posix()
    return [
        "`", "``", "```", "````", "a`` b ``c", "x ``` y ``` z", "` both `",
        "$", "$$", "$x$", "x$$ y $$z", "\\$", "\\\\$", "\\", "trail\\",
        "\r", "\r\n", "\r\r", "\n", "\n\n", "\t", "\u00a0", "\u00a0\u00a0\u00a0\u00a0",
        "\u200b", "\u200d", "\ufeff", "\u2028", "\u2029", "\x0b", "\x0c", "\x85",
        "\ue000", "\ue001", "\ue0000\ue001", "\ue0007\ue001", "\U000f0001",
        f"![x]({url})", f"![x](<{url}>)", f"[l]({url})", f"<{url}>", url,
        f"![s]({path})", f"![s](<{secret}>)", "![s3](../secret.png)",
        f'<img src="{url}">', f'<img src="file:///{path}">', "<b>raw</b>",
        "<!-- c -->", "<script>x</script>",
        "# h", "## h", "> q", "- li", "* li", "+ li", "1. li", "1) li",
        "    indented", "\tindented", "---", "***", "===", "- [ ] task",
        "```py", "~~~", "```{=openxml}", "~~~ {=openxml}",
        "---\ncsl: " + url + "\n---", f"---\ntitle: x\ncsl: {path}\n---",
        "`<w:p/>`{=openxml}", "[x]{custom-style=\"Title\"}", "{.underline}",
        "[x]: " + url, "[x]", "[@x]", "@x", "[^1]", "[^1]: note",
        "www.example.com", "mailto:a@b.c", "a@b.com", "https://e.com/x",
        "*em*", "**b**", "_u_", "~~s~~", "^sup^", "|a|b|", "\\[", "!{}[",
        "<{}", "&amp;", "&#x21;[", "%21%5B",
        # Click-only linkify paths: a citation that renders as nothing,
        # and a combining mark or joiner right before a domain's dot.
        "see e.[chunk:99999]com/x", "see e.[note:abc]com/x",
        "x\u0301.com/x", "e\u200d.com/x",
    ]


def _payloads(port: int, secret: Path) -> list[str]:
    frags = _fragments(port, secret)
    seps = ["", " ", "\n", "\r", "\r\n", "\t", "\u00a0"]
    rng = random.Random(20261117)
    mixed = ["".join(rng.choice(frags) + rng.choice(seps)
                     for _ in range(rng.randint(2, 6))) for _ in range(40)]
    return frags + mixed


def _txt(text: str, **styles: Any) -> dict[str, Any]:
    return {"type": "text", "text": text, "styles": styles}


def _blk(bid: str, type: str, *, props: dict[str, Any] | None = None,
         content: Any = None) -> Block:
    return Block(id=bid, note_id=1, parent_id=None, type=type,  # type: ignore[arg-type]
                 props=props or {}, content=content, text="", author="user",
                 section_key=None, changed_rev=1)


FIELDS = (
    "title", "section", "heading", "text", "bold", "italic", "strike",
    "underline", "code", "hidden", "comment", "link_text", "href", "mention",
    "bullet", "numbered", "check", "quote", "callout", "code_block", "language",
    "math", "pq_quote", "pq_lang", "pq_orig", "pq_section", "fig_caption",
    "eq_caption", "eq_latex", "region_caption", "paper_title", "author",
    "arxiv", "table_head", "table_cell", "table_math", "table_code")


def _fixture(v: dict[str, str], tag: str = "") -> tuple[list[Block], ExportPaper]:
    """A note whose user fields hold ``v[field]`` ("x" when unset), in the
    editor's shape: a reference block's pieces and source as JSON strings.
    Block ids get ``tag``."""
    f = {k: v.get(k, "x") for k in FIELDS}
    runs = [_txt(f["text"]), _txt(f["bold"], bold=True),
            _txt(f["italic"], italic=True), _txt(f["strike"], strike=True),
            _txt(f["underline"], underline=True), _txt(f["code"], code=True),
            _txt(f["hidden"], hidden=True), _txt(f["comment"], comment=True),
            {"type": "link", "href": f["href"], "content": [_txt(f["link_text"])]},
            {"type": "mention", "props": {"label": f["mention"], "paperId": 5}}]
    rects = [[0.1, 0.1, 0.5, 0.2]]
    blocks = [
        _blk(f"s{tag}", "section", props={"title": f["section"]}),
        _blk(f"h{tag}", "heading", props={"level": 1}, content=[_txt(f["heading"])]),
        _blk(f"p{tag}", "paragraph", content=runs),
        _blk(f"b{tag}", "bulletListItem", content=[_txt(f["bullet"])]),
        _blk(f"n{tag}", "numberedListItem", content=[_txt(f["numbered"])]),
        _blk(f"c{tag}", "checkListItem", props={"checked": True},
             content=[_txt(f["check"])]),
        _blk(f"q{tag}", "quote", content=[_txt(f["quote"])]),
        _blk(f"o{tag}", "callout", content=[_txt(f["callout"], code=True)]),
        _blk(f"k{tag}", "codeBlock", props={"language": f["language"]},
             content=[_txt(f["code_block"])]),
        _blk(f"m{tag}", "math", props={"latex": f["math"]}),
        _blk(f"tb{tag}", "table", content={
            "type": "tableContent", "columnWidths": [None, None],
            "headerRows": 1, "rows": [
                {"cells": [[_txt(f["table_head"], bold=True)], f["table_head"]]},
                {"cells": [runs, [_txt(f["table_cell"]),
                                  {"type": "inlineMath",
                                   "props": {"latex": f["table_math"]}},
                                  _txt(f["table_code"], code=True)]]}]}),
        _blk(f"pq{tag}", "paperQuote", props={
            "paperContentId": 5, "quote": "q", "lang": f["pq_lang"],
            "orig": f["pq_orig"],
            "pieces": json.dumps([{"quote": f["pq_quote"], "page": 0,
                                   "rects": rects}]),
            "source": json.dumps({"section": f["pq_section"], "page": 0})}),
        _blk(f"pf{tag}", "paperFigure", props={
            "paperContentId": 5, "quote": f["fig_caption"],
            "pieces": json.dumps([{"page": 0, "rects": rects}])}),
        _blk(f"pe{tag}", "paperEquation", props={
            "paperContentId": 5, "quote": f["eq_caption"],
            "pieces": json.dumps([{"latex": f["eq_latex"], "page": 0}])}),
        _blk(f"pr{tag}", "paperRegion", props={
            "paperContentId": 5, "quote": f["region_caption"]}),
    ]
    paper = ExportPaper(id=5, title=f["paper_title"] or "x",
                        authors=[f["author"], "Ann Lee"], year=2017,
                        arxiv_id=f["arxiv"])
    return blocks, paper


def _everywhere(p: str) -> dict[str, str]:
    return dict.fromkeys(FIELDS, p)


@pytest.fixture
def own_images(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Figures and equations get the exporter's own image; regions none."""
    def image(self: Any, b: Block, pid: int | None) -> tuple[str, bytes] | None:
        if b.type in ("paperFigure", "paperEquation"):
            return self._name(".png"), _png()
        return None
    monkeypatch.setattr(note_export._Writer, "image", image)
    yield


def _export(v: dict[str, str]) -> tuple[str, dict[str, bytes], str]:
    blocks, paper = _fixture(v)
    note = Note(id=1, target_kind="paper", target_id="5",
                title=v.get("title", "x"), revision=1, updated_at="")
    return note_to_markdown(note, blocks, {5: paper})


def _flat(tokens: list[Token]) -> Iterator[Token]:
    for t in tokens:
        yield t
        if t.children:
            yield from _flat(t.children)


# Blocks the exporter writes itself, which note text must never add to
# (bounded by a plain "x" note).
MD_BLOCKS = ("blockquote_open", "bullet_list_open", "ordered_list_open", "hr",
             "table_open", "tr_open", "th_open", "td_open")
PANDOC_BLOCKS = ("BlockQuote", "BulletList", "OrderedList", "HorizontalRule",
                 "Table", "DefinitionList", "LineBlock", "Div", "Note", "Span")
# linkify on, as in VS Code's preview: a bare domain would count as a link.
# The table rule is on (GFM has it): without it a pipe-table break would
# go undetected.
_LINKIFY = MarkdownIt("commonmark", {"linkify": True}).enable(["linkify", "table"])


def _md_kinds(md: str) -> list[str]:
    return [t.type for t in _flat(_LINKIFY.parse(md))]


def _outside_math(tokens: list[Token]) -> Iterator[Token]:
    """Every token, less the linkify links inside a ``$$…$$`` paragraph: a
    math-unaware viewer may make a bare URL in LaTeX clickable, which
    neither fetches nor adds structure, and escaping it would break the
    LaTeX (``\\label{eq.a}``)."""
    prev = ""
    for t in tokens:
        yield t
        was, prev = prev, t.type
        if not t.children:
            continue
        # A ``$$…$$`` paragraph, or a table cell holding inline ``$…$``.
        in_math = t.type == "inline" and (
            t.content.startswith("$$")
            or (was in ("td_open", "th_open") and "$" in t.content))
        skip = False
        for c in t.children:
            if in_math and c.type == "link_open" and c.markup == "linkify":
                skip = True
                continue
            if skip:
                skip = c.type != "link_close"
                continue
            yield c


def _check_markdown_it(md: str, base: list[str]) -> None:
    tokens = list(_outside_math(_LINKIFY.parse(md)))
    kinds = [t.type for t in tokens]
    for k in MD_BLOCKS:
        assert kinds.count(k) <= base.count(k), (k, md)
    assert kinds.count("heading_open") == HEADINGS, md
    assert kinds.count("link_open") == LINKS, md
    assert kinds.count("fence") == CODE_BLOCKS and "code_block" not in kinds, md
    assert {str(t.attrGet("src")) for t in tokens if t.type == "image"} == OWN_IMAGES, md
    assert kinds.count("image") == len(OWN_IMAGES), md
    assert "html_block" not in kinds and "html_inline" not in kinds, md


def _pandoc_nodes(md: str, reader: str) -> list[dict[str, Any]]:
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


def _task_marker(link: dict[str, Any], nodes: list[dict[str, Any]]) -> bool:
    """A check item's own ``[ ]`` / ``[x]`` that commonmark_x's
    implicit_header_references reads as a link to a heading of that text:
    the exporter's marker, pointing inside the document."""
    ids = {"#" + n["c"][1][0] for n in nodes if n["t"] == "Header"}
    text = "".join(i.get("c", " ") if i["t"] == "Str" else " "
                   for i in link["c"][1])
    return link["c"][2][0] in ids and text.strip() in ("", "x", "X")


def _attrs(nodes: list[dict[str, Any]]) -> set[str]:
    return {json.dumps(n["c"][0]) for n in nodes if n["t"] in ("Span", "Div")}


def _check_pandoc(md: str, bib: str, reader: str,
                  base: list[dict[str, Any]]) -> None:
    nodes = _pandoc_nodes(md, reader)
    kinds = [n["t"] for n in nodes]
    base_kinds = [n["t"] for n in base]
    for k in PANDOC_BLOCKS:
        # No footnote, styled span or div, quote, list or rule beyond the
        # exporter's own (its underline span is the only Span).
        assert kinds.count(k) <= base_kinds.count(k), (reader, k, md)
    assert _attrs(nodes) <= _attrs(base), (reader, md)
    assert kinds.count("Header") == HEADINGS, (reader, md)
    links = [n for n in nodes if n["t"] == "Link" and not _task_marker(n, nodes)]
    assert len(links) == LINKS, (reader, md)
    assert kinds.count("CodeBlock") == CODE_BLOCKS, (reader, md)
    images = [n["c"][2][0] for n in nodes if n["t"] == "Image"]
    assert sorted(images) == sorted(OWN_IMAGES), (reader, md)
    assert "RawInline" not in kinds and "RawBlock" not in kinds, (reader, md)
    keys = set(re.findall(r"@misc\{([^,]+),", bib))
    cited = {c["citationId"] for n in nodes if n["t"] == "Cite" for c in n["c"][0]}
    assert cited <= keys, (reader, md)


@pytest.fixture(scope="module")
def secret(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("secret") / "secret.png"
    path.write_bytes(_png() + SECRET.encode())
    return path


PORT = 9  # discard: never reached by note.md parsing (no fetch at parse time)


def _png() -> bytes:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page(width=20, height=20)  # type: ignore[no-untyped-call]
    png = bytes(page.get_pixmap().tobytes("png"))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]
    return png


def _cases() -> list[str]:
    return _payloads(PORT, Path("C:/probe/secret.png"))


_BASE: dict[str, Any] = {}


def _base_md() -> list[str]:
    if "md" not in _BASE:
        _BASE["md"] = _md_kinds(_export({})[0])
    return list(_BASE["md"])


def _base_pandoc(reader: str) -> list[dict[str, Any]]:
    if reader not in _BASE:
        _BASE[reader] = _pandoc_nodes(_export({})[0], reader)
    return list(_BASE[reader])


def _per_field_cases(n: int | None = None) -> list[tuple[str, str]]:
    """Every fixed fragment in each field alone (or a seeded sample)."""
    frags = _fragments(PORT, Path("C:/probe/secret.png"))
    cases = [(f, p) for p in frags for f in FIELDS]
    return cases if n is None else random.Random(7).sample(cases, n)


@pytest.mark.usefixtures("own_images")
@pytest.mark.parametrize("payload", _cases(), ids=lambda p: repr(p)[:40])
def test_markdown_renders_only_own_structure(payload: str) -> None:
    md, images, _bib = _export(_everywhere(payload))
    assert set(images) == OWN_IMAGES
    _check_markdown_it(md, _base_md())


@pytest.mark.usefixtures("own_images")
def test_markdown_per_field_renders_only_own_structure() -> None:
    """Each fixed fragment in one field at a time, the others plain."""
    base = _base_md()
    for field, payload in _per_field_cases():
        md, _images, _bib = _export({field: payload})
        try:
            _check_markdown_it(md, base)
        except AssertionError as exc:
            raise AssertionError((field, payload)) from exc


@needs_pandoc
@pytest.mark.usefixtures("own_images")
@pytest.mark.parametrize("payload", _cases(), ids=lambda p: repr(p)[:40])
def test_pandoc_readers_see_only_own_structure(payload: str) -> None:
    md, _images, bib = _export(_everywhere(payload))
    for reader in PANDOC_READERS:
        _check_pandoc(md, bib, reader, _base_pandoc(reader))


@needs_pandoc
@pytest.mark.usefixtures("own_images")
@pytest.mark.parametrize(("field", "payload"), _per_field_cases(40),
                         ids=lambda v: repr(v)[:30])
def test_pandoc_per_field_sees_only_own_structure(field: str, payload: str) -> None:
    """A seeded sample of the per-field cases through pandoc's readers."""
    md, _images, bib = _export({field: payload})
    for reader in PANDOC_READERS:
        _check_pandoc(md, bib, reader, _base_pandoc(reader))


class _Listener:
    """A local HTTP server counting the requests made to it."""

    def __init__(self) -> None:
        hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<style/>")

            def log_message(self, *args: Any) -> None:
                pass

        self.hits = hits
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Listener:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


@needs_pandoc
@pytest.mark.usefixtures("own_images")
def test_docx_fetches_nothing_and_reads_no_local_file(secret: Path) -> None:
    with _Listener() as srv:
        blocks: list[Block] = []
        papers: dict[int, ExportPaper] = {}
        for i, p in enumerate(_payloads(srv.port, secret)):
            more, paper = _fixture(_everywhere(p), tag=f"x{i}")
            blocks += more
            papers[5] = paper
        note = Note(id=1, target_kind="paper", target_id="5",
                    title=f"---\ncsl: http://127.0.0.1:{srv.port}/t.csl\n---",
                    revision=1, updated_at="")
        md, images, bib = note_to_markdown(note, blocks, papers)
        docx = markdown_to_docx(md, {**images, "references.bib": bib.encode()})
    assert srv.hits == []
    own = set(images.values())
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        for name in z.namelist():
            data = z.read(name)
            assert SECRET.encode() not in data, name
            if name.startswith("word/media/"):
                assert data in own, name
