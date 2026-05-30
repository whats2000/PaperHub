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
    author: str = ""
    date: str = ""
    subtitle: str = ""
    # F4.4 T4: deduplicated paper-defined ``\newcommand`` /
    # ``\renewcommand`` / ``\DeclareMathOperator`` block, already wrapped
    # with the ``% BEGIN/END paperhub:paper_newcommands`` markers by
    # :func:`paperhub.agents._newcommands.build_newcommands_block`.
    # Inserted AFTER any ``ADDITIONAL.tex`` macros and BEFORE ``\title{}``
    # so paper-defined macros are visible everywhere in the deck.
    paper_newcommands_block: str = ""


def build_additional_block(macros: list[str]) -> str:
    if not macros:
        return ""
    return "\n".join(macros)


def build_graphicspath(cache_source_dirs: list[str]) -> str:
    if not cache_source_dirs:
        return ""
    dirs = " ".join(
        "{" + d.replace("\\", "/").rstrip("/") + "/}" for d in cache_source_dirs
    )
    return f"\\graphicspath{{ {dirs} }}"


def assemble_deck(inp: AssembleInput) -> str:
    preamble: list[str] = [
        "\\documentclass{beamer}",
        f"\\usetheme{{{inp.theme}}}",
        "\\usepackage{graphicx}",
        "\\usepackage{booktabs}",
        "\\usepackage{amsmath,amssymb}",
        build_graphicspath(inp.cache_source_dirs),
        build_additional_block(inp.additional_tex_macros),
        inp.paper_newcommands_block,
        f"\\title{{{inp.title}}}",
    ]
    if inp.subtitle:
        preamble.append(f"\\subtitle{{{inp.subtitle}}}")
    if inp.author:
        preamble.append(f"\\author{{{inp.author}}}")
    if inp.date:
        preamble.append(f"\\date{{{inp.date}}}")
    parts: list[str] = [
        *preamble,
        "\\begin{document}",
        # Real, editable title frame (not bare \maketitle) so its layout can be
        # customized via the edit_title sub-flow (F4.2).
        "\\begin{frame}[plain]\n\\titlepage\n\\end{frame}",
        *inp.frames,
        "\\end{document}",
    ]
    return "\n".join(p for p in parts if p) + "\n"
