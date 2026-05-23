"""Figure index utilities for the slide pipeline.

Walks paper cache source dirs to collect the real image files present on disk,
so the LaTeX assembler can:
  1. emit ``\\graphicspath`` entries for the actual subdirectories that contain
     images (Bug B part 1 — ``\\graphicspath`` is not recursive).
  2. neutralize any ``\\includegraphics{name}`` whose stem is not a real file,
     replacing it with a safe text placeholder (Bug B part 2 — LLM hallucination
     of figure names causes fatal ``File not found`` compile errors).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_IMAGE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"}

_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}")


@dataclass(frozen=True)
class FigureIndex:
    dirs: list[str]   # forward-slashed dirs that CONTAIN image files (for \\graphicspath)
    stems: set[str]   # figure basenames WITHOUT extension (what \\includegraphics{X} references)


def collect_figures(cache_source_dirs: list[str]) -> FigureIndex:
    """Walk each cache source dir recursively; collect the directories that contain
    image files (forward-slashed, deduped, sorted) and the set of image basename stems.

    Non-existent paths are silently skipped.
    """
    dirs: set[str] = set()
    stems: set[str] = set()
    for root in cache_source_dirs:
        base = Path(root)
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
                dirs.add(p.parent.as_posix())
                stems.add(p.stem)
    return FigureIndex(dirs=sorted(dirs), stems=stems)


def neutralize_unknown_graphics(tex: str, known_stems: set[str]) -> str:
    """Replace any ``\\includegraphics{name}`` whose basename stem is NOT a known real
    figure with a plain text placeholder, so a hallucinated/missing figure can never
    cause a fatal ``File not found`` compile error.

    ``\\includegraphics{model}``       → kept (stem "model" is known)
    ``\\includegraphics{model.pdf}``   → kept (stem "model" is known)
    ``\\includegraphics[w=.5\\textwidth]{ghost}`` → ``\\textit{[figure omitted: ghost]}``
    """

    def _repl(m: re.Match[str]) -> str:
        name = m.group(2).strip()
        stem = Path(name).stem
        if stem in known_stems:
            return m.group(0)
        return f"\\textit{{[figure omitted: {Path(name).name}]}}"

    return _INCLUDEGRAPHICS_RE.sub(_repl, tex)
