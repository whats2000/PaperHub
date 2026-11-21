"""Markdown -> Word through pandoc (an optional dependency; the Word export
is unavailable without it). Modelled on ``table_figures._render_pandoc``.

Note text is untrusted input to pandoc. What holds:

- The reader is plain Markdown plus only the extensions the exporter's own
  output uses (``_READER``): no YAML metadata or title blocks, so document
  text can set no metadata (no ``csl:``, ``bibliography:``, ``references:``)
  and citeproc reads only the ``references.bib`` named on the command line;
  no raw HTML / TeX / attribute output.
- The note is read in a first pandoc run with ``--sandbox`` (no file and no
  URL beyond the command line), writing pandoc's JSON AST. The .docx is
  written from that AST in a second run without ``--sandbox``: a
  distribution pandoc (Debian/Ubuntu, built without embedded data files)
  builds its default ``reference.docx`` from its data directory, which the
  sandboxed writer cannot read ("Could not find data file", exit 97).
- Between the runs the AST is rewritten here (``_own_images_only``): an
  image naming one of the given resources points at that resource's copy,
  saved under an index name (never a name from the note); every other
  image becomes its alt text. So the writer reads exactly the given
  resources, and no image path or URL in the note is ever read.
- After the second run the tables are drawn in the viewer's table style
  (``style_docx_tables``): pandoc's own ``word/document.xml`` is rewritten
  to give each table its borders (three-line or grid). Then each table
  caption gets its "Table N." (a Word SEQ field) and a table's notes
  paragraph (put in the Table Note style in the AST) is drawn small
  (``docx_table_captions``). No reference docx is involved, so neither run
  reads anything more.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape as xml_escape

PANDOC_TIMEOUT_S = 60
BIB_NAME = "references.bib"
# ``--sandbox`` arrived in pandoc 2.15.
MIN_VERSION = (2, 15)
# The version is re-read after this long (an upgrade shows without restart).
VERSION_TTL_S = 60.0
# Strict Markdown, without raw HTML, plus what the exporter writes: ATX
# headings and quotes after blank lines, backtick fences, task lists,
# strikeout, the underline span, hard breaks, figures, citations, math.
_READER = (
    "markdown_strict-raw_html-shortcut_reference_links-spaced_reference_links"
    "+all_symbols_escapable+intraword_underscores+space_in_atx_header"
    "+blank_before_blockquote+blank_before_header+backtick_code_blocks"
    "+task_lists+strikeout+bracketed_spans+escaped_line_breaks"
    "+implicit_figures+citations+tex_math_dollars+footnotes+pipe_tables"
    # A table's caption (the exporter's ``Table: <caption>`` line).
    "+table_captions"
    # Typography and heading anchors: neither sets metadata or paths.
    "+smart+auto_identifiers")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")

PandocStatus = Literal["ok", "pandoc_missing", "pandoc_too_old"]
TableStyle = Literal["threeLine", "grid"]
TABLE_STYLES: tuple[TableStyle, ...] = ("threeLine", "grid")
# The Word paragraph style of a table's notes (styleId without the space).
TABLE_NOTE_STYLE = "Table Note"


class PandocError(RuntimeError):
    """pandoc is absent, failed or did not finish in time, or was handed a
    resource it must not read. ``stderr`` holds pandoc's own messages,
    which may quote note text: kept for a debugger, never logged."""

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def pandoc_available() -> bool:
    return shutil.which("pandoc") is not None


def _read_version(exe: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15,
                             check=False).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"(\d+(?:\.\d+)+)", out.splitlines()[0] if out else "")
    return tuple(int(v) for v in m[1].split(".")) if m else None


_clock = time.monotonic
_versions: dict[str, tuple[float, tuple[int, ...] | None]] = {}


def reset_version_cache() -> None:
    _versions.clear()


def pandoc_version() -> tuple[int, ...] | None:
    """The installed pandoc's version (None when absent or unreadable),
    re-read at most every ``VERSION_TTL_S`` seconds."""
    exe = shutil.which("pandoc")
    if not exe:
        return None
    hit = _versions.get(exe)
    if hit is not None and _clock() - hit[0] < VERSION_TTL_S:
        return hit[1]
    version = _read_version(exe)
    _versions[exe] = (_clock(), version)
    return version


def pandoc_status() -> PandocStatus:
    """Whether the Word export can run here, and why not."""
    if not pandoc_available():
        return "pandoc_missing"
    version = pandoc_version()
    if version is None or version < MIN_VERSION:
        return "pandoc_too_old"
    return "ok"


def _safe(name: str) -> str:
    if not _NAME.match(name) or ".." in name.split("/"):
        raise PandocError(f"unsafe resource name {name!r}")
    return name


def _own_images_only(node: Any, files: dict[str, str]) -> Any:
    """``node`` (pandoc JSON AST) with each image naming a key of ``files``
    pointed at that file, every other image replaced by its alt text, and
    every heading id prefixed with ``h-``: the prefix no citeproc id has
    (``refs``, ``ref-<key>``), so a heading titled "refs" never shares the
    bibliography's bookmark."""
    if isinstance(node, list):
        out: list[Any] = []
        for item in node:
            if isinstance(item, dict) and item.get("t") == "Image":
                attr, alt, (src, title) = item["c"]
                if src not in files:
                    out.extend(_own_images_only(alt, files))
                    continue
                item = {"t": "Image", "c": [attr, _own_images_only(alt, files),
                                            [files[src], title]]}
                out.append(item)
                continue
            out.append(_own_images_only(item, files))
        return out
    if isinstance(node, dict):
        if node.get("t") == "Header":
            level, (ident, classes, kvs), inlines = node["c"]
            return {"t": "Header", "c": [
                level, ["h-" + ident if ident else ident, classes, kvs],
                _own_images_only(inlines, files)]}
        return {k: _own_images_only(v, files) for k, v in node.items()}
    return node


def _mark_table_notes(node: Any, flags: Sequence[bool], seen: list[int]) -> None:
    """In ``node`` (pandoc JSON AST, changed in place), the paragraph right
    after the n-th table, when ``flags[n]``, put in the Table Note style.
    ``seen`` counts the tables passed (document order)."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, dict) and item.get("t") == "Table":
                n = seen[0]
                seen[0] += 1
                nxt = node[i + 1] if i + 1 < len(node) else None
                if (n < len(flags) and flags[n] and isinstance(nxt, dict)
                        and nxt.get("t") == "Para"):
                    node[i + 1] = {"t": "Div", "c": [
                        ["", [], [["custom-style", TABLE_NOTE_STYLE]]], [nxt]]}
                continue
            _mark_table_notes(item, flags, seen)
    elif isinstance(node, dict):
        for v in node.values():
            _mark_table_notes(v, flags, seen)


def markdown_to_docx(md: str, resources: dict[str, bytes], *,
                     table_style: TableStyle = "threeLine",
                     table_label: str = "Table",
                     table_notes: Sequence[bool] = ()) -> bytes:
    """The ``.docx`` of ``md``. ``resources`` are the files it refers to by
    relative path (images, and ``references.bib`` for its ``[@key]``
    citations, which pandoc's citeproc then renders with a reference list).
    Its tables are drawn in ``table_style``; each caption is numbered
    "``table_label`` N.", and the paragraph after the n-th table is its
    notes (drawn small) when ``table_notes[n]``."""
    if table_style not in TABLE_STYLES:
        raise PandocError(f"unknown table style {table_style!r}")
    names = [_safe(n) for n in resources]
    images = [n for n in names if n != BIB_NAME]
    try:
        workdir = tempfile.TemporaryDirectory()
    except OSError as exc:
        raise PandocError(f"could not make a temp dir: {exc!r}") from exc
    with workdir as tmp:
        root = Path(tmp)
        # Each image is saved under its index, never its own name (a name
        # like CON or AUX is a device on Windows); the AST is pointed at it.
        files = {n: f"res/{i:04d}{_ext(n)}" for i, n in enumerate(images, 1)}
        bib = resources.get(BIB_NAME, b"")
        try:
            (root / "res").mkdir()
            for name, file in files.items():
                (root / file).write_bytes(resources[name])
            if bib.strip():
                (root / BIB_NAME).write_bytes(bib)
            (root / "note.md").write_text(md, encoding="utf-8")
        except OSError as exc:
            raise PandocError(f"could not stage the export: {exc!r}") from exc
        # The untrusted note is parsed sandboxed; the writer gets the AST.
        ast = _run(root, ["pandoc", "note.md", "--sandbox", "--from", _READER,
                          "--to", "json", "--output", "note.json"], "note.json")
        try:
            doc = _own_images_only(json.loads(ast), files)
            _mark_table_notes(doc.get("blocks", []), table_notes, [0])
            (root / "note.json").write_text(json.dumps(doc), encoding="utf-8")
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise PandocError(f"could not rewrite the AST: {exc!r}") from exc
        args = ["pandoc", "note.json", "--from", "json",
                "--to", "docx", "--output", "note.docx"]
        if bib.strip():
            args += ["--citeproc", "--bibliography", BIB_NAME]
        styled = style_docx_tables(_run(root, args, "note.docx"), table_style)
        return docx_table_captions(styled, table_label)


# Table borders, in eighths of a point: booktabs' heavy top / bottom rule
# and its lighter mid rule under the header; a thin line for the grid.
_RULE_HEAVY = 12
_RULE_LIGHT = 6
_GRID = 4
_HEADER_TINT = "F2F2F2"
# Child order the schema requires (CT_TblPr / CT_TcPr): Word may refuse a
# document whose properties are out of order.
_TBLPR_ORDER = ("tblStyle", "tblpPr", "tblOverlap", "bidiVisual",
                "tblStyleRowBandSize", "tblStyleColBandSize", "tblW", "jc",
                "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout",
                "tblCellMar", "tblLook", "tblCaption", "tblDescription")
_TCPR_ORDER = ("cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge",
               "tcBorders", "shd", "noWrap", "tcMar", "textDirection",
               "tcFitText", "vAlign", "hideMark")
# CT_RPr: the header's bold goes after a run's style and fonts.
_RPR_ORDER = ("rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps",
              "strike", "dstrike", "outline", "shadow", "emboss", "imprint",
              "noProof", "snapToGrid", "vanish", "webHidden", "color",
              "spacing", "w", "kern", "position", "sz", "szCs", "highlight",
              "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs",
              "em", "lang", "eastAsianLayout", "specVanish", "oMath")
_CHILD = re.compile(r"<w:(\w+)\b[^>]*?/>|<w:(\w+)\b[^>]*>.*?</w:\2>", re.S)


def _line(side: str, val: str, sz: int = 0) -> str:
    if val == "nil":
        return f'<w:{side} w:val="nil" />'
    return f'<w:{side} w:val="{val}" w:sz="{sz}" w:space="0" w:color="auto" />'


def _table_borders(style: TableStyle) -> str:
    if style == "grid":
        sides = "".join(_line(s, "single", _GRID) for s in
                        ("top", "left", "bottom", "right", "insideH", "insideV"))
    else:
        sides = (_line("top", "single", _RULE_HEAVY) + _line("left", "nil")
                 + _line("bottom", "single", _RULE_HEAVY) + _line("right", "nil")
                 + _line("insideH", "nil") + _line("insideV", "nil"))
    return f"<w:tblBorders>{sides}</w:tblBorders>"


def _header_cell(style: TableStyle) -> str:
    if style == "grid":
        return f'<w:shd w:val="clear" w:color="auto" w:fill="{_HEADER_TINT}" />'
    return f"<w:tcBorders>{_line('bottom', 'single', _RULE_LIGHT)}</w:tcBorders>"


def _with_child(inner: str, child: str, order: tuple[str, ...]) -> str:
    """``inner`` (a properties element's children) with ``child`` added
    (replacing one of its name) and every child in schema order."""
    name = re.match(r"<w:(\w+)", child)[1]  # type: ignore[index]
    parts = [(m[1] or m[2], m[0]) for m in _CHILD.finditer(inner)]
    if "".join(p for _, p in parts) != re.sub(r">\s+<", "><", inner.strip()):
        # Something this does not parse: the child goes at the end, in
        # place of any of its name (never two tblBorders / shd).
        same = rf"<w:{name}(?=[\s/>])[^>]*?/>|<w:{name}(?=[\s/>])[^>]*>.*?</w:{name}>"
        return re.sub(same, "", inner, flags=re.S) + child
    parts = [p for p in parts if p[0] != name] + [(name, child)]
    rank = {n: i for i, n in enumerate(order)}
    parts.sort(key=lambda p: rank.get(p[0], len(order)))
    return "".join(p for _, p in parts)


def _props(xml: str, tag: str, child: str, order: tuple[str, ...]) -> str:
    """``xml`` (one element's opening and contents) with ``child`` in its
    leading ``<w:{tag}>``, added when there is none."""
    m = re.match(rf"(<w:\w+[^>]*>)(<w:{tag}\s*/>|<w:{tag}>(.*?)</w:{tag}>)?",
                 xml, re.S)
    if m is None:
        raise PandocError(f"no element to give a <w:{tag}>")
    inner = m[3] or ""
    return (f"{m[1]}<w:{tag}>{_with_child(inner, child, order)}</w:{tag}>"
            + xml[m.end():])


def _style_table(tbl: str, style: TableStyle) -> str:
    tbl = _props(tbl, "tblPr", _table_borders(style), _TBLPR_ORDER)
    # The header: the row pandoc marks (repeated on each page), else the
    # first row: pandoc before 2.20 may not mark it, and every exported
    # pipe table has a header row (an empty one when the note's has none).
    rows = list(re.finditer(r"<w:tr[ >].*?</w:tr>", tbl, re.S))
    if not rows:
        return tbl
    row = next((r for r in rows if "<w:tblHeader" in r[0]), rows[0])
    cell = _header_cell(style)
    head = re.sub(r"<w:tc>.*?</w:tc>",
                  lambda c: _props(c[0], "tcPr", cell, _TCPR_ORDER),
                  row[0], flags=re.S)
    # Its text is bold, as in the editor.
    head = re.sub(r"<w:r>.*?</w:r>",
                  lambda r: _props(r[0], "rPr", "<w:b />", _RPR_ORDER),
                  head, flags=re.S)
    return tbl[:row.start()] + head + tbl[row.end():]


def style_docx_tables(docx: bytes, style: TableStyle) -> bytes:
    """``docx`` (pandoc's) with every table drawn in ``style``: three-line
    (a heavy rule above the header, a light one under it, a heavy one at
    the end; no other lines) or grid (every border, the header tinted);
    the header's text is bold in both.
    pandoc writes the XML, so note text in it is always escaped: no tag
    matched here can come from the note."""
    if style not in TABLE_STYLES:
        raise PandocError(f"unknown table style {style!r}")
    try:
        src = zipfile.ZipFile(io.BytesIO(docx))
    except zipfile.BadZipFile as exc:
        raise PandocError(f"could not read the .docx: {exc!r}") from exc
    with src:
        try:
            xml = src.read("word/document.xml").decode("utf-8")
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
            raise PandocError(f"could not read the .docx: {exc!r}") from exc
        if "<w:tbl>" not in xml:
            return docx
        # pandoc's cells hold paragraphs only, never a nested table.
        xml = re.sub(r"<w:tbl>.*?</w:tbl>",
                     lambda t: _style_table(t[0], style), xml, flags=re.S)
        return _with_document(src, xml)


def _with_document(src: zipfile.ZipFile, xml: str) -> bytes:
    """``src`` with ``word/document.xml`` replaced by ``xml``, every other
    part (and the parts' order and compression) as it was."""
    return _with_parts(src, {"word/document.xml": xml})


def _with_parts(src: zipfile.ZipFile, parts: dict[str, str]) -> bytes:
    """``src`` with each part named in ``parts`` replaced, every other part
    (and the parts' order and compression) as it was."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            text = parts.get(info.filename)
            data = text.encode("utf-8") if text is not None else src.read(info)
            dst.writestr(info, data, compress_type=info.compress_type)
    return out.getvalue()


# A table's caption paragraph (pandoc's style) and its notes paragraph
# (``_mark_table_notes``): the notes are small and grey, as in a journal.
_CAPTION_PARA = re.compile(
    r'(<w:p>\s*<w:pPr>(?:(?!</w:pPr>|</w:p>).)*?<w:pStyle w:val="TableCaption"\s*/>'
    r'.*?</w:pPr>)(.*?</w:p>)', re.S)
_TABLE_NOTE_RPR = ('<w:color w:val="595959" />', '<w:sz w:val="18" />',
                   '<w:szCs w:val="18" />')


def _caption_prefix(label: str, n: int) -> str:
    """"<label> N. " in bold, N a Word SEQ field (so Word renumbers it)."""
    bold = "<w:rPr><w:b /></w:rPr>"
    return (f'<w:r>{bold}<w:t xml:space="preserve">{xml_escape(label)} </w:t></w:r>'
            r'<w:fldSimple w:instr=" SEQ Table \* ARABIC ">'
            f"<w:r>{bold}<w:t>{n}</w:t></w:r></w:fldSimple>"
            f'<w:r>{bold}<w:t xml:space="preserve">. </w:t></w:r>')


def _small_table_notes(styles: str) -> str:
    style = re.search(
        rf'<w:style [^>]*w:styleId="{TABLE_NOTE_STYLE.replace(" ", "")}"[^>]*>(.*?)</w:style>',
        styles, re.S)
    if style is None:
        return styles
    inner = style[1]
    rpr = re.search(r"<w:rPr>(.*?)</w:rPr>|<w:rPr\s*/>", inner, re.S)
    props = (rpr[1] or "") if rpr else ""
    for child in _TABLE_NOTE_RPR:
        props = _with_child(props, child, _RPR_ORDER)
    inner = (inner[:rpr.start()] + f"<w:rPr>{props}</w:rPr>" + inner[rpr.end():]
             if rpr else inner + f"<w:rPr>{props}</w:rPr>")
    return styles[:style.start(1)] + inner + styles[style.end(1):]


def docx_table_captions(docx: bytes, label: str) -> bytes:
    """``docx`` (pandoc's) with each table caption opened by a bold
    "``label`` N." (N counting the captions) and the Table Note style drawn
    small and grey. pandoc writes the XML, so note text in it is escaped:
    no tag matched here can come from the note."""
    try:
        src = zipfile.ZipFile(io.BytesIO(docx))
    except zipfile.BadZipFile as exc:
        raise PandocError(f"could not read the .docx: {exc!r}") from exc
    with src:
        try:
            xml = src.read("word/document.xml").decode("utf-8")
            names = set(src.namelist())
            styles = (src.read("word/styles.xml").decode("utf-8")
                      if "word/styles.xml" in names else None)
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
            raise PandocError(f"could not read the .docx: {exc!r}") from exc
        count = [0]

        def number(m: re.Match[str]) -> str:
            if "SEQ Table" in m[2]:
                return m[0]
            count[0] += 1
            return m[1] + _caption_prefix(label, count[0]) + m[2]

        parts = {"word/document.xml": _CAPTION_PARA.sub(number, xml)}
        if styles is not None:
            parts["word/styles.xml"] = _small_table_notes(styles)
        if parts["word/document.xml"] == xml and parts.get("word/styles.xml") == styles:
            return docx
        return _with_parts(src, parts)


def _run(root: Path, args: list[str], output: str) -> bytes:
    """Run pandoc in ``root`` and return ``output``'s bytes."""
    try:
        proc = subprocess.run(
            args, cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=PANDOC_TIMEOUT_S, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise PandocError(f"pandoc did not run: {exc!r}") from exc
    out = root / output
    if proc.returncode != 0 or not out.is_file():
        # pandoc's messages quote the input (note text), so the error
        # names only the exit code and the stderr's length and hash;
        # the text itself stays on the exception, never in a log line.
        err = proc.stderr or ""
        digest = hashlib.sha256(err.encode("utf-8", "replace")).hexdigest()[:12]
        raise PandocError(
            f"pandoc exited {proc.returncode}: stderr {len(err)} chars, "
            f"sha256 {digest}", stderr=err)
    return out.read_bytes()


def _ext(name: str) -> str:
    ext = Path(name).suffix.lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ""
