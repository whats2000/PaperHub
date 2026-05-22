"""Assemble a Beamer deck from generated section frames (new code).

Writes the preamble (theme + ADDITIONAL.tex), title frame, all section frames,
and a single \\graphicspath spanning every contributing paper's cache source
dir (SRS v2.18 §III-5.3 step 4a). Figures are never copied into the session dir.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AssembleInput:
    title: str
    theme: str
    additional_tex_macros: list[str]
    cache_source_dirs: list[str]
    frames: list[str]


def build_additional_block(macros: list[str]) -> str:
    if not macros:
        return ""
    return "\n".join(macros)


def build_graphicspath(cache_source_dirs: list[str]) -> str:
    if not cache_source_dirs:
        return ""
    dirs = " ".join("{" + d.rstrip("/") + "/}" for d in cache_source_dirs)
    return f"\\graphicspath{{ {dirs} }}"


def assemble_deck(inp: AssembleInput) -> str:
    parts: list[str] = [
        "\\documentclass{beamer}",
        f"\\usetheme{{{inp.theme}}}",
        "\\usepackage{graphicx}",
        "\\usepackage{booktabs}",
        "\\usepackage{amsmath,amssymb}",
        build_graphicspath(inp.cache_source_dirs),
        build_additional_block(inp.additional_tex_macros),
        f"\\title{{{inp.title}}}",
        "\\begin{document}",
        "\\maketitle",
        *inp.frames,
        "\\end{document}",
    ]
    return "\n".join(p for p in parts if p) + "\n"
