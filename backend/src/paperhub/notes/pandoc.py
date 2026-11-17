"""Markdown -> Word through pandoc (an optional dependency; the Word export
is unavailable without it). Modelled on ``table_figures._render_pandoc``.

Note text is untrusted input to pandoc, so pandoc runs with ``--sandbox``
(no file or network access beyond its command line) and without the raw
HTML / TeX / attribute extensions. The exporter's own images reach the
.docx through a Lua filter that puts exactly the given resources into
pandoc's media bag; any other image reference stays unfetched.
"""
from __future__ import annotations

import functools
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

PANDOC_TIMEOUT_S = 60
BIB_NAME = "references.bib"
# ``--sandbox`` arrived in pandoc 2.15.
MIN_VERSION = (2, 15)
_READER = "markdown-raw_html-raw_tex-raw_attribute"
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


@functools.lru_cache(maxsize=4)
def _version_of(exe: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15,
                             check=False).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"(\d+(?:\.\d+)+)", out.splitlines()[0] if out else "")
    return tuple(int(v) for v in m[1].split(".")) if m else None


def pandoc_version() -> tuple[int, ...] | None:
    """The installed pandoc's version, None when absent or unreadable."""
    exe = shutil.which("pandoc")
    return _version_of(exe) if exe else None


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


def _media_filter(names: list[str]) -> str:
    """A Lua filter adding exactly ``names`` (files in pandoc's working dir)
    to the media bag, so the sandboxed writer embeds them."""
    items = ", ".join(f'{{"{n}", "{_MIME.get(Path(n).suffix.lower(), "")}"}}'
                      for n in names)
    return (
        f"local items = {{{items}}}\n"
        "function Pandoc(doc)\n"
        "  for _, it in ipairs(items) do\n"
        "    local f = io.open(it[1], 'rb')\n"
        "    if f then\n"
        "      local mime = it[2] ~= '' and it[2] or nil\n"
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
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in names:
            target = root.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resources[name])
        (root / "note.md").write_text(md, encoding="utf-8")
        images = [n for n in names if n != BIB_NAME]
        (root / "media.lua").write_text(_media_filter(images), encoding="utf-8")
        args = ["pandoc", "note.md", "--sandbox", "--from", _READER,
                "--to", "docx", "--output", "note.docx",
                "--lua-filter", "media.lua"]
        if resources.get(BIB_NAME, b"").strip():
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
