"""Markdown -> Word through pandoc (an optional dependency; the Word export
is unavailable without it). Modelled on ``table_figures._render_pandoc``.

Note text is untrusted input to pandoc. What holds:

- The reader is plain Markdown plus only the extensions the exporter's own
  output uses (``_READER``): no YAML metadata or title blocks, so document
  text can set no metadata (no ``csl:``, ``bibliography:``, ``references:``)
  and citeproc reads only the ``references.bib`` named on the command line;
  no raw HTML / TeX / attribute output.
- pandoc runs with ``--sandbox``: its reader and writer read no file and no
  URL beyond the command line. The exporter's own images reach the .docx
  through a Lua filter that puts exactly the given resources into pandoc's
  media bag (the filter is not sandboxed, and reads only those files, saved
  under index names, never a name from the note); any other image
  reference stays unfetched.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal

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
    "+implicit_figures+citations+tex_math_dollars")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
         ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
         ".pdf": "application/pdf", ".emf": "image/emf"}

PandocStatus = Literal["ok", "pandoc_missing", "pandoc_too_old"]


class PandocError(RuntimeError):
    """pandoc is absent, failed or did not finish in time, or was handed a
    resource it must not read."""


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


def _media_filter(items: list[tuple[str, str]]) -> str:
    """A Lua filter adding each ``(media name, file)`` to the media bag, so
    the sandboxed writer embeds exactly these. Files are pandoc-cwd
    relative; names and files are checked by ``_safe``."""
    rows = ", ".join(
        f'{{"{name}", "{file}", "{_MIME.get(Path(name).suffix.lower(), "")}"}}'
        for name, file in items)
    return (
        f"local items = {{{rows}}}\n"
        "function Pandoc(doc)\n"
        "  for _, it in ipairs(items) do\n"
        "    local f = io.open(it[2], 'rb')\n"
        "    if f then\n"
        "      local mime = it[3] ~= '' and it[3] or nil\n"
        "      pandoc.mediabag.insert(it[1], mime, f:read('a'))\n"
        "      f:close()\n"
        "    end\n"
        "  end\n"
        "  return doc\n"
        "end\n")


def markdown_to_docx(md: str, resources: dict[str, bytes]) -> bytes:
    """The ``.docx`` of ``md``. ``resources`` are the files it refers to by
    relative path (images, and ``references.bib`` for its ``[@key]``
    citations, which pandoc's citeproc then renders with a reference list)."""
    names = [_safe(n) for n in resources]
    images = [n for n in names if n != BIB_NAME]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Each image is saved under its index, never its own name (a name
        # like CON or AUX is a device on Windows); the filter maps it back.
        items = [(n, f"res/{i:04d}{_ext(n)}") for i, n in enumerate(images, 1)]
        bib = resources.get(BIB_NAME, b"")
        try:
            (root / "res").mkdir()
            for name, file in items:
                (root / file).write_bytes(resources[name])
            if bib.strip():
                (root / BIB_NAME).write_bytes(bib)
            (root / "note.md").write_text(md, encoding="utf-8")
            (root / "media.lua").write_text(_media_filter(items), encoding="utf-8")
        except OSError as exc:
            raise PandocError(f"could not stage the export: {exc!r}") from exc
        args = ["pandoc", "note.md", "--sandbox", "--from", _READER,
                "--to", "docx", "--output", "note.docx",
                "--lua-filter", "media.lua"]
        if bib.strip():
            args += ["--citeproc", "--bibliography", BIB_NAME]
        try:
            proc = subprocess.run(
                args, cwd=root, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=PANDOC_TIMEOUT_S, check=False)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            raise PandocError(f"pandoc did not run: {exc!r}") from exc
        out = root / "note.docx"
        if proc.returncode != 0 or not out.is_file():
            raise PandocError(
                f"pandoc exited {proc.returncode}: {proc.stderr[:500]}")
        return out.read_bytes()


def _ext(name: str) -> str:
    ext = Path(name).suffix.lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ""
