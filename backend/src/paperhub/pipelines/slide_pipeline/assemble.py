"""Assemble a Beamer deck from generated section frames (new code).

Writes the preamble (theme + ADDITIONAL.tex), title frame, all section frames,
and a single \\graphicspath spanning every contributing paper's cache source
dir (SRS v2.18 §III-5.3 step 4a). Figures are never copied into the session dir.

F4.4 T7: the default preamble profile is the Final_Report gold methodology
(Berlin / dolphin / professionalfonts / 14pt / 16:9 + accent colors +
custom footline + booktabs/mathtools/tikz). The legacy minimal preamble
(``\\documentclass{beamer}`` + ``\\usetheme{metropolis}``) is preserved
under ``theme="metropolis"`` for parity / debugging. Unknown theme values
fall back to ``"gold"`` so a stray env-var typo cannot silently produce a
deck under an unrelated theme.
"""
from __future__ import annotations

from dataclasses import dataclass

# Recognised preamble-profile names. Anything else falls back to ``GOLD``.
GOLD = "gold"
METROPOLIS = "metropolis"
_KNOWN_THEMES = frozenset({GOLD, METROPOLIS})


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
    # F4.4 T5 review-fix: when True, do NOT prepend the auto-injected
    # ``\begin{frame}[plain]\titlepage\end{frame}`` — the caller has
    # already supplied a title frame in ``frames``. T3's ``title``
    # pattern template emits exactly that frame, and the T5 planner
    # ALWAYS emits a ``title`` PlannedSlide as slide #1, so without this
    # toggle the deck would have TWO leading identical title pages.
    # Default ``False`` preserves the pre-T5 behaviour for callers that
    # do not supply a title frame themselves.
    skip_title_injection: bool = False


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


def _build_metropolis_preamble_head() -> list[str]:
    """Legacy minimal preamble — preserved for ``theme="metropolis"`` parity."""
    return [
        "\\documentclass{beamer}",
        "\\usetheme{metropolis}",
        "\\usepackage{graphicx}",
        "\\usepackage{booktabs}",
        "\\usepackage{amsmath,amssymb}",
    ]


def _build_gold_preamble_head() -> list[str]:
    """F4.4 T7 default: the Final_Report gold methodology preamble.

    Verbatim port of ``D:/GitHub/Final_Report/slides.tex`` lines 1-35 minus
    the deck-specific watermark (which baked a hardcoded ``nycu.png`` and an
    ID-3-3 footer string). Layout/colors/footline/theme are the gold's;
    figures + title metadata are still filled by the caller as before.
    """
    return [
        "\\documentclass[aspectratio=169,14pt]{beamer}",
        "\\usepackage[T1]{fontenc}",
        "\\usepackage{textcomp}",
        "\\usepackage{graphicx}",
        "\\usepackage{booktabs}",
        "\\usepackage{mathtools,amssymb}",
        "\\usepackage{amsmath}",
        "\\usepackage{bm}",
        "\\usepackage{xcolor}",
        "\\usepackage{tikz}",
        "",
        "\\usetheme{Berlin}",
        "\\usecolortheme{dolphin}",
        "\\usefonttheme{professionalfonts}",
        "",
        "\\definecolor{accent}{RGB}{0,90,160}",
        "\\definecolor{accent2}{RGB}{200,60,60}",
        "\\definecolor{lightgray}{RGB}{240,240,240}",
        "",
        "\\setbeamercolor{block title}{bg=accent,fg=white}",
        "\\setbeamercolor{block body}{bg=lightgray,fg=black}",
        "\\setbeamertemplate{navigation symbols}{}",
        "\\setbeamersize{text margin left=0.6cm, text margin right=0.6cm}",
        "",
        "\\setbeamertemplate{footline}{",
        "  \\leavevmode%",
        "  \\hbox{%",
        "  \\begin{beamercolorbox}"
        "[wd=.5\\paperwidth,ht=2.25ex,dp=1ex,right]"
        "{title in head/foot}%",
        "    \\usebeamerfont{title in head/foot}"
        "\\insertshorttitle\\hspace*{2ex}",
        "  \\end{beamercolorbox}%",
        "  \\begin{beamercolorbox}"
        "[wd=.5\\paperwidth,ht=2.25ex,dp=1ex,left]"
        "{date in head/foot}%",
        "    \\usebeamerfont{date in head/foot}"
        "\\hspace*{2ex}\\hfill"
        "\\insertframenumber{} / \\inserttotalframenumber"
        "\\hspace*{2ex}",
        "  \\end{beamercolorbox}}%",
        "  \\vskip0pt%",
        "}",
    ]


def _resolve_theme(name: str) -> str:
    """Normalise + fall back: unknown values become ``GOLD`` (the default).

    A stray env-var typo (``PAPERHUB_SLIDE_THEME=goldd``) silently producing
    a metropolis deck would surprise the operator; falling back to the
    default keeps the surprise small."""
    norm = (name or "").strip().lower()
    return norm if norm in _KNOWN_THEMES else GOLD


def assemble_deck(inp: AssembleInput) -> str:
    theme = _resolve_theme(inp.theme)
    head = (
        _build_metropolis_preamble_head()
        if theme == METROPOLIS
        else _build_gold_preamble_head()
    )

    preamble: list[str] = [
        *head,
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
    ]
    if not inp.skip_title_injection:
        # Real, editable title frame (not bare \maketitle) so its layout can be
        # customized via the edit_title sub-flow (F4.2). Skipped when the
        # caller has already supplied a title frame in ``frames`` (F4.4 T5
        # planner ALWAYS emits one); otherwise the deck would carry two.
        parts.append("\\begin{frame}[plain]\n\\titlepage\n\\end{frame}")
    parts.extend(inp.frames)
    parts.append("\\end{document}")
    return "\n".join(p for p in parts if p) + "\n"
