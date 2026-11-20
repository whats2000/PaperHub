"""Note export hardening: note text can never pull files or URLs into the
export or inject raw markup, and the edge cases of text escaping."""
from __future__ import annotations

import http.server
import io
import re
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any

import aiosqlite
import pymupdf
import pytest

from paperhub.notes import export as note_export
from paperhub.notes.export import ExportPaper, export_sources, note_to_markdown
from paperhub.notes.models import Block, Note
from paperhub.notes.pandoc import PandocError, markdown_to_docx
from paperhub.pipelines.paper_asset import FigureAsset, PaperAsset, write_paper_asset

NOTE = Note(id=1, target_kind="paper", target_id="5", title="T",
            revision=1, updated_at="")
CHAT = Note(id=2, target_kind="session", target_id="1", title="Chat",
            revision=1, updated_at="")
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None,
                                  reason="pandoc not installed")


def _blk(bid: str, type: str, *, props: dict[str, Any] | None = None,
         content: Any = None) -> Block:
    return Block(id=bid, note_id=1, parent_id=None, type=type,  # type: ignore[arg-type]
                 props=props or {}, content=content, text="", author="user",
                 section_key=None, changed_rev=1)


def _txt(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "styles": {}}


def _link(href: str, label: str = "pic") -> dict[str, Any]:
    return {"type": "link", "href": href, "content": [_txt(label)]}


def _paper(pid: int, title: str, authors: list[str] | None = None,
           year: int | None = 2016, source_dir: Path | None = None) -> ExportPaper:
    return ExportPaper(id=pid, title=title, authors=authors or ["Kaiming He"],
                       year=year, source_dir=source_dir)


_UNESCAPED_IMAGE = re.compile(r"(?<!\\)!\[")


def _png() -> bytes:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page(width=50, height=50)  # type: ignore[no-untyped-call]
    png = bytes(page.get_pixmap().tobytes("png"))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]
    return png


def test_user_text_never_forms_an_image() -> None:
    blocks = [
        _blk("p1", "paragraph", content=[_txt("![x](C:/secret.png)")]),
        _blk("p2", "paragraph", content=[_txt("look!"), _link("C:/secret.png")]),
        _blk("p3", "paragraph", content=[_txt("![x](http://example.com/x.png)")]),
        _blk("p4", "paragraph", content=[_txt("!"), _link("http://example.com/y.png")]),
    ]
    md, images, _bib = note_to_markdown(NOTE, blocks, {})
    assert images == {}
    assert not _UNESCAPED_IMAGE.search(md), md
    assert r"\!\[x\](C\:/secret\.png)" in md
    assert r"look\![pic](<C:/secret.png>)" in md


def test_link_target_cannot_break_out_of_its_brackets() -> None:
    md, _i, _b = note_to_markdown(NOTE, [_blk("p1", "paragraph", content=[
        _link("http://a.example/x> ![y](C:/secret.png)")])], {})
    assert not _UNESCAPED_IMAGE.search(md), md
    assert "[pic](<http://a.example/x%3E%20%21%5By%5D%28C:/secret.png%29>)" in md


def test_code_language_is_validated() -> None:
    blocks = [
        _blk("k1", "codeBlock", props={"language": "{=openxml}"},
             content=[_txt("<w:p><w:r><w:t>INJECTED</w:t></w:r></w:p>")]),
        _blk("k2", "codeBlock", props={"language": "c++"}, content=[_txt("x")]),
        _blk("k3", "codeBlock", props={"language": "py {.x}"}, content=[_txt("y")]),
    ]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    assert "```\n<w:p>" in md and "openxml" not in md
    assert "```c++\nx\n```" in md
    assert "```\ny\n```" in md


@needs_pandoc
def test_docx_carries_no_injected_openxml() -> None:
    blocks = [_blk("k1", "codeBlock", props={"language": "{=openxml}"},
                   content=[_txt("<w:p><w:r><w:t>INJECTED</w:t></w:r></w:p>")]),
              _blk("p1", "paragraph",
                   content=[_txt("`{=openxml}` <w:p><w:r><w:t>RAW</w:t></w:r></w:p>")])]
    md, images, _b = note_to_markdown(NOTE, blocks, {})
    with zipfile.ZipFile(io.BytesIO(markdown_to_docx(md, images))) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "<w:t>INJECTED</w:t></w:r></w:p>" not in xml
    assert "<w:t>RAW</w:t></w:r></w:p>" not in xml


@needs_pandoc
def test_pandoc_sandbox_embeds_only_own_images(tmp_path: Path) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(_png())
    own = _png()
    md = (f"# T\n\n![own](images/f1.png)\n\n![s]({secret.as_posix()})\n\n"
          f"![s2](<{secret}>)\n\n![s3](../secret.png)\n")
    docx = markdown_to_docx(md, {"images/f1.png": own})
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        media = [z.read(n) for n in z.namelist() if n.startswith("word/media/")]
    assert media == [own]


def test_resource_names_must_stay_in_the_temp_dir() -> None:
    for bad in ("../x.png", "/etc/x.png", "C:/x.png", 'images/"x.png'):
        with pytest.raises(PandocError):
            markdown_to_docx("# x", {bad: b"x"})


def test_reference_block_in_chat_note_resolves_paper_through_mark() -> None:
    q = _blk("q1", "paperQuote", props={
        "markId": "mk_1", "kind": "quote", "quote": "Deep nets are hard to train.",
        "pieces": [{"quote": "Deep nets are hard to train.", "page": 0}],
        "source": {"page": 0, "section": "1 Introduction"}})
    md, _i, bib = note_to_markdown(
        CHAT, [q], {7: _paper(7, "Deep Residual Learning")},
        mark_papers={"mk_1": 7})
    assert "> — Deep Residual Learning, §1, p. 1 [@he2016deep]" in md
    assert "@misc{he2016deep," in bib


async def test_export_sources_loads_the_mark_paper(
        conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, sha256, title, "
        "authors_json, year, source_path, source_dir_path, html_path) VALUES "
        "(7, 'k7', 'pdf_upload', 'abc', 'Deep Residual Learning', "
        "'[\"Kaiming He\"]', 2016, 's', 'd', 'h')")
    await conn.execute(
        "INSERT INTO note_marks (id, paper_content_id, block_id, kind, view, "
        "pieces_json, created_at) VALUES ('mk_1', 7, 'q1', 'text', 'pdf', '[]', 'now')")
    await conn.commit()
    q = _blk("q1", "paperQuote", props={"markId": "mk_1", "quote": "x"})
    papers, chunk_papers, mark_papers, cites = await export_sources(conn, CHAT, [q])
    assert mark_papers == {"mk_1": 7}
    assert papers[7].title == "Deep Residual Learning" and chunk_papers == {}
    assert cites == {}


async def test_export_sources_loads_inline_citations_of_live_marks(
        conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, sha256, title, "
        "authors_json, year, source_path, source_dir_path, html_path) VALUES "
        "(7, 'k7', 'pdf_upload', 'abc', 'Deep Residual Learning', "
        "'[\"Kaiming He\"]', 2016, 's', 'd', 'h')")
    for mid, deleted in (("mk_1", None), ("mk_2", "then")):
        await conn.execute(
            "INSERT INTO note_marks (id, paper_content_id, block_id, kind, view, "
            "pieces_json, section_key, created_at, deleted_at) VALUES "
            "(?, 7, ?, 'text', 'pdf', ?, '4 Results', 'now', ?)",
            (mid, f"q_{mid}", '[{"quote": "rose to 62%", "page": 3}]', deleted))
    await conn.commit()
    p = _blk("p1", "paragraph", content=[
        {"type": "markCite", "props": {"markId": "mk_1", "text": "62%"}},
        {"type": "markCite", "props": {"markId": "mk_2", "text": "x"}}])
    papers, _c, _m, cites = await export_sources(conn, CHAT, [p])
    assert set(cites) == {"mk_1"}  # a deleted mark's citation has no source
    assert cites["mk_1"].quote == "rose to 62%" and cites["mk_1"].page == 3
    assert cites["mk_1"].section == "4 Results" and 7 in papers


def test_line_breaks_kept_and_line_starts_escaped() -> None:
    md, _i, _b = note_to_markdown(NOTE, [_blk("p1", "paragraph", content=[
        _txt("line1\n- not a list\n# not heading\n2. not numbered\n---")])], {})
    assert ("line1\\\n\\- not a list\\\n\\# not heading\\\n2\\. not numbered"
            "\\\n\\---") in md


def test_dollar_text_is_literal_but_math_blocks_are_math() -> None:
    blocks = [_blk("p1", "paragraph", content=[_txt("costs $5 and *$10")]),
              _blk("m1", "math", props={"latex": "x^2"})]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    assert r"costs \$5 and \*\$10" in md
    assert "$$x^2$$" in md


def test_cite_key_suffixes_only_over_cited_papers() -> None:
    papers = {1: _paper(1, "Deep Residual Learning"),
              2: _paper(2, "Deep Networks Again"),
              3: _paper(3, "Deep Thoughts")}
    md, _i, bib = note_to_markdown(CHAT, [_blk("p1", "paragraph", content=[
        _txt("a [fig:3/f] b [fig:2/f]")])], papers)
    # Paper 1 shares the base key but is not cited: no suffix is spent on it.
    assert "a [@he2016deep] b [@he2016deepa]" in md
    assert bib.count("@misc{") == 2
    assert "@misc{he2016deep,\n  title = {{Deep Thoughts}}" in bib
    assert "@misc{he2016deepa,\n  title = {{Deep Networks Again}}" in bib


def test_oversized_figure_image_is_unavailable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "cache"
    write_paper_asset(PaperAsset(figures=[FigureAsset(
        id="fig-1", caption="Big", page=0, section=None,
        image_path="figures/fig-1.png")]), src)
    (src / "asset" / "figures").mkdir(parents=True, exist_ok=True)
    (src / "asset" / "figures" / "fig-1.png").write_bytes(b"\x89PNG" + b"0" * 100)
    monkeypatch.setattr(note_export, "MAX_IMAGE_BYTES", 50)
    fig = _blk("f1", "paperFigure", props={
        "kind": "figure", "quote": "Big", "pieces": [{"figure_id": "fig-1"}]})
    md, images, _b = note_to_markdown(
        NOTE, [fig], {5: _paper(5, "P", source_dir=src)})
    assert images == {}
    assert "Big\n\n[image unavailable]" in md


# --- fix round 2 -------------------------------------------------------------

class _Listener:
    """A local HTTP server counting the requests pandoc makes to it."""

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


def _cited_paragraph() -> Block:
    return _blk("c1", "paragraph", content=[_txt("As shown [fig:7/f].")])


def _docx_text(md: str, images: dict[str, bytes], bib: str) -> str:
    docx = markdown_to_docx(md, {**images, "references.bib": bib.encode()})
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        return z.read("word/document.xml").decode("utf-8")


@needs_pandoc
def test_yaml_metadata_in_titles_fetches_nothing() -> None:
    with _Listener() as srv:
        evil = f"x\n\n---\ncsl: http://127.0.0.1:{srv.port}/evil.csl\n---\n"
        note = Note(id=3, target_kind="session", target_id="1", title=evil,
                    revision=1, updated_at="")
        blocks = [_blk("s1", "section", props={"title": evil}), _cited_paragraph()]
        md, images, bib = note_to_markdown(
            note, blocks, {7: _paper(7, "Deep Residual Learning")})
        assert "\n---\ncsl" not in md  # the titles are folded onto one line
        xml = _docx_text(md, images, bib)
        # Even raw document text with a metadata block sets nothing.
        _docx_text(f"---\ncsl: http://127.0.0.1:{srv.port}/raw.csl\n---\n\n"
                   "# T\n\nAs shown [@he2016deep].\n", {}, bib)
    assert srv.hits == []
    assert "He" in xml  # citeproc still rendered the citation


@needs_pandoc
def test_csl_local_path_reads_nothing(tmp_path: Path,
                                      caplog: pytest.LogCaptureFixture) -> None:
    csl = tmp_path / "local.csl"
    csl.write_text("LOCALCSL-SECRET", encoding="utf-8")
    md = (f"---\ncsl: {csl.as_posix()}\nbibliography: {csl.as_posix()}\n---\n\n"
          "# T\n\nAs shown [@he2016deep].\n")
    bib = "@misc{he2016deep,\n  title = {{Deep}},\n  year = {2016},\n}\n"
    try:
        xml = _docx_text(md, {}, bib)
    except PandocError as exc:
        pytest.fail(f"pandoc failed: {exc}")
    assert "LOCALCSL-SECRET" not in xml
    assert "LOCALCSL-SECRET" not in caplog.text


@needs_pandoc
def test_device_name_block_id_exports(tmp_path: Path) -> None:
    src = tmp_path / "cache"
    write_paper_asset(PaperAsset(figures=[FigureAsset(
        id="fig-1", caption="Cap", page=0, section=None,
        image_path="figures/fig-1.png")]), src)
    (src / "asset" / "figures").mkdir(parents=True, exist_ok=True)
    (src / "asset" / "figures" / "fig-1.png").write_bytes(_png())
    blocks = [_blk(bid, "paperFigure", props={
        "kind": "figure", "quote": "Cap", "pieces": [{"figure_id": "fig-1"}]})
        for bid in ("CON", "NUL", "AUX", "COM1")]
    md, images, bib = note_to_markdown(
        NOTE, blocks, {5: _paper(5, "P", source_dir=src)})
    assert sorted(images) == [f"images/{i:04d}.png" for i in range(1, 5)]
    assert "CON" not in md
    docx = markdown_to_docx(md, {**images, "references.bib": bib.encode()})
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        assert len([n for n in z.namelist() if n.startswith("word/media/")]) == 4
    # A caller's own device-name resource is written under a safe name too.
    docx = markdown_to_docx("![x](images/CON.png)\n", {"images/CON.png": _png()})
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        assert len([n for n in z.namelist() if n.startswith("word/media/")]) == 1


def test_temp_write_failure_is_a_pandoc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Path, data: bytes) -> int:
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(PandocError):
        markdown_to_docx("# x", {"images/0001.png": b"x"})


def test_math_cannot_close_early_or_form_markup() -> None:
    blocks = [
        _blk("m1", "math", props={"latex": "x$$ ![z](http://example.com/z.png) $$y"}),
        _blk("e1", "paperEquation", props={"kind": "equation", "quote": "q", "pieces": [
            {"latex": "a$ <http://example.com/a> ![w](http://example.com/w.png)"}]}),
    ]
    md, _i, _b = note_to_markdown(NOTE, blocks, {})
    assert "![" not in md and "<http" not in md, md
    body = md.split("\n", 1)[1]
    # Each math span opens and closes exactly once.
    for line in (ln for ln in body.split("\n") if ln.startswith("$$")):
        assert line.count("$$") == 2 and r"\$" in line


def test_version_check_is_cached_briefly(monkeypatch: pytest.MonkeyPatch) -> None:
    from paperhub.notes import pandoc as note_pandoc
    calls: list[str] = []
    now = [1000.0]
    monkeypatch.setattr(note_pandoc.shutil, "which", lambda _n: "/bin/pandoc")
    monkeypatch.setattr(note_pandoc, "_read_version",
                        lambda exe: calls.append(exe) or (3, 1))
    monkeypatch.setattr(note_pandoc, "_clock", lambda: now[0])
    note_pandoc.reset_version_cache()
    assert note_pandoc.pandoc_version() == (3, 1)
    now[0] += 30
    assert note_pandoc.pandoc_version() == (3, 1)
    assert len(calls) == 1
    now[0] += 31
    note_pandoc.pandoc_version()
    assert len(calls) == 2
    note_pandoc.reset_version_cache()


def test_leading_spaces_never_make_a_code_block() -> None:
    md, _i, _b = note_to_markdown(NOTE, [
        _blk("p1", "paragraph", content=[_txt("    indented start\n\tand a tab")]),
        _blk("l1", "bulletListItem", content=[_txt("      item")]),
    ], {})
    body = md.split("\n", 1)[1]
    assert not re.search(r"^(?: {4}|\t)", body, re.M), body
    assert "\u00a0\u00a0\u00a0\u00a0indented start" in md
    assert "- \u00a0\u00a0\u00a0\u00a0\u00a0\u00a0item" in md


def test_temp_dir_failure_is_a_pandoc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from paperhub.notes import pandoc as note_pandoc

    def no_dir(*_a: Any, **_k: Any) -> Any:
        raise OSError("no space left for a temp dir")
    monkeypatch.setattr(note_pandoc.tempfile, "TemporaryDirectory", no_dir)
    with pytest.raises(PandocError):
        markdown_to_docx("# x", {})


@pytest.mark.parametrize("runs", [
    ["see e.", "com/x"], ["www", ".example.org"], ["a /", "/b.org/x"],
    ["e.com", " and x.org"],
])
def test_no_bare_link_across_or_within_runs(runs: list[str]) -> None:
    from markdown_it import MarkdownIt
    content = [{"type": "text", "text": runs[0], "styles": {}},
               {"type": "text", "text": runs[1], "styles": {"comment": True}}]
    md, _i, _b = note_to_markdown(NOTE, [_blk("p1", "paragraph", content=content)], {})
    html = MarkdownIt("commonmark", {"linkify": True}).enable("linkify").render(md)
    assert "<a " not in html, (md, html)
    assert "".join(runs) in html, html  # renders as typed


@pytest.mark.parametrize("content", [
    # A text run, then a mention whose label carries the rest of a domain.
    [_txt("see e."), {"type": "mention", "props": {"label": "com/x"}}],
    # A citation that resolves to nothing joins the halves around it.
    [_txt("see e.[chunk:99999]com/x")],
    [_txt("see e.[note:abc]com/x")],
    # A combining mark or a zero-width joiner right before the dot.
    [_txt("see x\u0301.com/x")],
    [_txt("see e\u200d.com/x")],
], ids=["mention", "dropped-chunk", "dropped-note", "combining-mark", "zwj"])
def test_no_bare_link_through_mentions_dropped_citations_or_marks(
        content: list[dict[str, Any]]) -> None:
    from markdown_it import MarkdownIt
    md, _i, _b = note_to_markdown(NOTE, [_blk("p1", "paragraph", content=content)], {})
    html = MarkdownIt("commonmark", {"linkify": True}).enable("linkify").render(md)
    assert "<a " not in html, (md, html)


def test_download_name_drops_direction_marks() -> None:
    name = note_export.display_name("a\u200fb.exe\u200e x\u061c")
    assert not set(name) & {"\u200e", "\u200f", "\u061c"}, repr(name)


# --- distribution pandoc (no embedded data files) ----------------------------

def test_note_text_is_read_only_by_a_sandboxed_run(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A distro pandoc's sandboxed docx writer cannot read its own data
    dir (exit 97), so the writer runs unsandboxed: the note's Markdown must
    then only ever reach pandoc through a ``--sandbox`` run, and the docx
    run reads the AST that run wrote."""
    from paperhub.notes import pandoc as note_pandoc
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, cwd: Path, **_kw: Any) -> Any:
        calls.append(args)
        out = args[args.index("--output") + 1]
        (cwd / out).write_bytes(
            b'{"pandoc-api-version":[1,23],"meta":{},"blocks":[]}'
            if out.endswith(".json") else b"x")
        return type("P", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(note_pandoc.subprocess, "run", fake_run)
    markdown_to_docx("# T\n", {"references.bib": b"@misc{a, title={A}}"})
    reads_md = [a for a in calls if "note.md" in a]
    assert reads_md and all("--sandbox" in a for a in reads_md)
    docx = [a for a in calls if "docx" in a]
    assert len(docx) == 1
    assert docx[0][1:4] == ["note.json", "--from", "json"]


@needs_pandoc
def test_unsandboxed_writer_fetches_no_image_url() -> None:
    with _Listener() as srv:
        md = (f"# T\n\n![u](http://127.0.0.1:{srv.port}/x.png)\n\n"
              f"![u2](<http://127.0.0.1:{srv.port}/y.png>)\n\nText.\n")
        docx = markdown_to_docx(md, {})
    assert srv.hits == []
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        assert not [n for n in z.namelist() if n.startswith("word/media/")]
