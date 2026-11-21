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
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

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
    # Typography and heading anchors: neither sets metadata or paths.
    "+smart+auto_identifiers")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")

PandocStatus = Literal["ok", "pandoc_missing", "pandoc_too_old"]


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


def markdown_to_docx(md: str, resources: dict[str, bytes]) -> bytes:
    """The ``.docx`` of ``md``. ``resources`` are the files it refers to by
    relative path (images, and ``references.bib`` for its ``[@key]``
    citations, which pandoc's citeproc then renders with a reference list)."""
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
            (root / "note.json").write_text(json.dumps(doc), encoding="utf-8")
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise PandocError(f"could not rewrite the AST: {exc!r}") from exc
        args = ["pandoc", "note.json", "--from", "json",
                "--to", "docx", "--output", "note.docx"]
        if bib.strip():
            args += ["--citeproc", "--bibliography", BIB_NAME]
        return _run(root, args, "note.docx")


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
