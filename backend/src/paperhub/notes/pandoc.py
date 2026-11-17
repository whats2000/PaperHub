"""Markdown -> Word through pandoc (an optional dependency; the Word export
is unavailable without it). Modelled on ``table_figures._render_pandoc``."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

PANDOC_TIMEOUT_S = 60
BIB_NAME = "references.bib"


class PandocError(RuntimeError):
    """pandoc is absent, failed or did not finish in time."""


def pandoc_available() -> bool:
    return shutil.which("pandoc") is not None


def _safe(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe resource name {name!r}")
    return path


def markdown_to_docx(md: str, resources: dict[str, bytes]) -> bytes:
    """The ``.docx`` of ``md``. ``resources`` are the files it refers to by
    relative path (images, and ``references.bib`` for its ``[@key]``
    citations, which pandoc's citeproc then renders with a reference list)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, data in resources.items():
            target = root.joinpath(*_safe(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (root / "note.md").write_text(md, encoding="utf-8")
        args = ["pandoc", "note.md", "--from", "markdown", "--to", "docx",
                "--output", "note.docx", "--resource-path", "."]
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
