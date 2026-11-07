"""Render paper source to HTML for the Citation Canvas (FR-03).

Strategy:
- LaTeX: pandoc primary (good math + figure support). pylatexenc fallback
  when pandoc is absent OR exits non-zero (idiosyncratic LaTeX is common).
- PDF: PyMuPDF's HTML export (preserves layout enough for highlight scrolling).
"""
from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import pymupdf
from pylatexenc.latex2text import LatexNodes2Text

from paperhub.pipelines.mathjax_macros import (
    MacroValue,
    build_mathjax_config_script,
)
from paperhub.pipelines.sentinels import find_math_spans, optional_arg_end

logger = logging.getLogger(__name__)

# pandoc --mathjax injects a bare MathJax loader <script> with no inline config.
# We splice our window.MathJax config in just before it. Matches the opening
# <script ...> whose attributes (which may span newlines, hence [^>]) reference
# mathjax — the loader tag, not the polyfill above it.
_MATHJAX_SCRIPT_RE = re.compile(r"<script\b(?=[^>]*?mathjax)")

_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)
# Inline base64 <img> emitted by PyMuPDF's get_text("html") for PDF pages.
_DATA_URI_IMG_RE = re.compile(
    r'(<img\b[^>]*?\bsrc=")data:image/([a-zA-Z0-9.+-]+);base64,([^"]+)(")',
    re.IGNORECASE,
)
_IMG_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
# data: URI subtype -> on-disk extension for extracted PDF page images.
_DATA_URI_EXT = {
    "png": ".png",
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "gif": ".gif",
    "svg+xml": ".svg",
    "webp": ".webp",
}

# pandoc can HANG (not just exit non-zero) on pathological LaTeX. Without a
# bound the hanging subprocess parks the whole ingest until the worker OOMs
# (arxiv:2410.12557 reproduced this). Cap it and treat a timeout like any
# other pandoc failure — fall back to pylatexenc, then the raw envelope.
_PANDOC_TIMEOUT_SECONDS = 60

# Max stray unclosed braces we'll delete before retrying pandoc. A real-world
# typo leaves 1 (arXiv:2406.07524 ships `\owt{` with no close); a large count
# signals something else (e.g. a verbatim miscount) where blind editing is
# unlikely to help, so we don't bother and fall back instead.
_MAX_BRACE_FIX = 16


def _unmatched_open_braces(text: str) -> list[int]:
    """Indices of unmatched opening ``{`` (escape- + comment-aware).

    pdflatex tolerates a stray unclosed brace — a common authoring typo
    (arXiv:2406.07524 ships ``\\owt{`` with no close) — by implicitly closing
    the group at ``\\end{document}``; pandoc's stricter parser instead rejects
    the whole document with "unexpected end of input". Returning the exact
    positions lets us delete the typo'd opener and retry pandoc.

    We delete the opener rather than append a closer at EOF: a trailing ``}``
    makes the stray ``{`` swallow the entire remainder of the paper into the
    preceding macro's argument (so pandoc renders only the prefix), whereas
    deleting the typo'd ``{`` renders the full document.
    """
    stack: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:  # escaped char (\{ \} \%) — skip both
            i += 2
            continue
        if c == "%":  # unescaped comment — skip to end of line
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            continue
        if c == "{":
            stack.append(i)
        elif c == "}" and stack:
            stack.pop()
        i += 1
    return stack


def _unclosed_braces(text: str) -> int:
    """Count of unmatched opening braces (see :func:`_unmatched_open_braces`)."""
    return len(_unmatched_open_braces(text))


# Max orphaned \end we'll delete before retrying pandoc — same rationale as
# _MAX_BRACE_FIX: a couple is an author typo, a flood signals something else.
_MAX_ENV_FIX = 16
_ENV_TOKEN_RE = re.compile(r"\\(begin|end)\{([^}]+)\}")


def _is_commented(text: str, pos: int) -> bool:
    r"""True if an unescaped ``%`` precedes ``pos`` on its line (so a token at
    ``pos`` is inside a LaTeX comment and invisible to pandoc)."""
    line_start = text.rfind("\n", 0, pos) + 1
    i = line_start
    while i < pos:
        if text[i] == "\\":  # escaped char (\%) — skip both
            i += 2
            continue
        if text[i] == "%":
            return True
        i += 1
    return False


def _orphaned_env_ends(text: str) -> list[tuple[int, int]]:
    r"""``(start, end)`` spans of every ``\end{X}`` with no live matching
    ``\begin{X}`` — comment-aware.

    A paper can ship a commented-out ``% \begin{table}`` while leaving the
    matching ``\end{table}`` live (arXiv:2501.02902 — an author typo). pdflatex
    tolerates it; pandoc correctly ignores the commented ``\begin`` and then
    aborts the WHOLE parse on the orphaned ``\end{table}`` ("unexpected \\end,
    expecting end of input"), so the paper degrades to the plain-text envelope.
    Deleting the orphaned ``\end`` lets pandoc render the rest as HTML. The
    in-between content (caption, tabular) renders as ordinary blocks."""
    stack: list[str] = []
    orphans: list[tuple[int, int]] = []
    for m in _ENV_TOKEN_RE.finditer(text):
        if _is_commented(text, m.start()):
            continue
        kind, env = m.group(1), m.group(2)
        if kind == "begin":
            stack.append(env)
        elif env in stack:
            while stack and stack[-1] != env:  # pop to the matching frame
                stack.pop()
            stack.pop()
        else:
            orphans.append((m.start(), m.end()))
    return orphans


# pandoc's LaTeX reader understands \newcommand/\def but NOT these package-
# specific definition forms, so a `#1` parameter reference inside one aborts the
# WHOLE parse with "unexpected #1" — the paper then degrades to the <pre> dump
# (arXiv:2404.07214's body `\newcolumntype{P}[1]{...#1...}`, arXiv:2603.03276's
# `\newtcolorbox{...}[1][]{... title=#1 ...}`). These commands DEFINE column
# types / coloured boxes and emit nothing themselves, so deleting the definition
# is safe: the document renders, and any `\begin{name}...\end{name}` usage that
# follows still emits its content as an ordinary block.
_MAX_DEF_FIX = 200
_HOSTILE_DEF_RE = re.compile(
    r"\\(?:newcolumntype|newtcolorbox|renewtcolorbox|newtcbox|renewtcbox"
    r"|DeclareTColorBox|NewTColorBox|NewTotalTColorBox|DeclareTotalTColorBox"
    r"|tcbset)(?![A-Za-z])"
)


def _skip_group(text: str, i: int, open_ch: str, close_ch: str) -> int:
    r"""Index just after the balanced ``open_ch``…``close_ch`` group starting at
    ``text[i]`` (escape-aware); returns ``i`` unchanged if ``text[i]`` isn't
    ``open_ch``."""
    if i >= len(text) or text[i] != open_ch:
        return i
    depth = 0
    while i < len(text):
        c = text[i]
        if c == "\\":  # escaped char — skip both
            i += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _pandoc_hostile_def_spans(text: str) -> list[tuple[int, int]]:
    r"""``(start, end)`` spans of every pandoc-hostile DEFINITION command —
    ``\newcolumntype`` and the tcolorbox ``\newtcolorbox`` family — whose ``#1``
    aborts pandoc's parse (see the ``_MAX_DEF_FIX`` block above). Comment-aware.
    Each span covers the command plus its following ``[..]``/``{..}`` argument
    groups, so the whole definition is removed as one unit."""
    spans: list[tuple[int, int]] = []
    for m in _HOSTILE_DEF_RE.finditer(text):
        if _is_commented(text, m.start()):
            continue
        j = m.end()
        for _ in range(6):  # \newtcolorbox takes at most ~5 bracketed/braced args
            k = j
            while k < len(text) and text[k] in " \t\n":
                k += 1
            if k < len(text) and text[k] == "[":
                j = _skip_group(text, k, "[", "]")
            elif k < len(text) and text[k] == "{":
                j = _skip_group(text, k, "{", "}")
            else:
                break
        spans.append((m.start(), j))
    return spans


# Symbol macros pandoc can't expand, which render as an EMPTY span. A check/cross
# inside a coloured wrapper drops to `<span style="color: red"></span>` — colour
# survives, glyph vanishes, cell shows nothing. Two flavours:
#  * pifont `\ding{NN}` used directly (`\textcolor{red}{\ding{55}}`,
#    arXiv:2506.10100's ablation table); and
#  * the custom `\cmark`/`\xmark` aliases (`\newcommand{\cmark}{\ding{51}}`,
#    arXiv:2509.22093) — by far the more common idiom — whose DEFINITION is
#    frequently outside the rendered body, so pandoc can't expand the usage.
# We substitute the Unicode glyph BEFORE pandoc. Applied to the render-time tex
# AFTER table rasterisation, so a table compiled to a PNG keeps native macros
# (pdflatex renders pifont/amssymb natively); only the HTML-rendered residue is
# rewritten. Unknown ding codes are left untouched (better a literal macro than a
# wrong glyph).
_PIFONT_DING_GLYPH = {
    "51": "✓",  # ✓ check mark
    "52": "✔",  # ✔ heavy check mark
    "53": "✕",  # ✕ multiplication x
    "54": "✖",  # ✖ heavy multiplication x
    "55": "✗",  # ✗ ballot x
    "56": "✘",  # ✘ heavy ballot x
    "72": "★",  # ★ black star
    "73": "☆",  # ☆ white star
}
_DING_RE = re.compile(r"\\ding\s*\{\s*(\d+)\s*\}")

# Standalone check / cross / Harvey-ball symbol macros that pandoc DROPS in text
# mode (it renders nothing -> an empty table cell). Map each to its Unicode glyph.
# Custom \newcommand aliases (\tick, \greencheck, \fullcirc, …) are already handled
# upstream by expand_preamble_macros; THIS map is for the STANDARD package commands
# pandoc can't render, which papers use bare in comparison tables:
#   * amssymb ``\checkmark``  (text-mode use -> empty cell, arXiv:2503.14734)
#   * the near-universal ``\cmark`` / ``\xmark`` aliases
#   * bbding ``\Checkmark`` / ``\XSolidBrush``; ``\ballotx``
#   * fontawesome ``\faCheck`` / ``\faTimes`` (+Circle/Square variants)
#   * wasysym Harvey balls ``\CIRCLE`` / ``\Circle`` / ``\LEFTcircle`` /
#     ``\RIGHTcircle`` (none / partial / full support -> ● ○ ◐ ◑)
# A math-mode use (``$\checkmark$``) becomes ``$glyph$`` which MathJax renders
# identically. The negative lookbehinds skip a DEFINING site so a definition that
# survives into the render tex isn't corrupted; the trailing word-boundary keeps
# longer names (``\cmarked``, ``\Circled``) intact.
_SYMBOL_MACRO_GLYPH = {
    "checkmark": "✓", "cmark": "✓", "Checkmark": "✓",
    "faCheck": "✓", "faCheckCircle": "✓", "faCheckSquare": "☑",
    "xmark": "✗", "XSolidBrush": "✗", "ballotx": "✗",
    "faTimes": "✗", "faTimesCircle": "✗",
    "Circle": "○", "emptycirc": "○",
    "LEFTcircle": "◐", "halfcirc": "◐",
    "RIGHTcircle": "◑",
    "CIRCLE": "●", "fullcirc": "●",
}
# Longer alternatives first so e.g. \faCheckCircle isn't shadowed by \faCheck.
_SYMBOL_MACRO_RE = re.compile(
    r"(?<!\\newcommand\{)(?<!\\renewcommand\{)(?<!\\providecommand\{)"
    r"(?<!\\DeclareRobustCommand\{)(?<!\\def)"
    r"\\(faCheckCircle|faCheckSquare|faTimesCircle|faCheck|faTimes|"
    r"XSolidBrush|Checkmark|checkmark|cmark|xmark|ballotx|"
    r"LEFTcircle|RIGHTcircle|CIRCLE|Circle|emptycirc|halfcirc|fullcirc)"
    r"(?![A-Za-z])"
)


def replace_symbol_macros(tex: str) -> str:
    r"""Rewrite check / cross / Harvey-ball symbol macros pandoc can't render to
    their Unicode glyph so they survive (pandoc drops the unknown macro, leaving
    an empty cell): pifont ``\ding{NN}`` plus the standard-package marks in
    ``_SYMBOL_MACRO_GLYPH`` (amssymb/bbding/fontawesome/wasysym + the
    ``\cmark``/``\xmark`` aliases).

    Runs on the render-time tex AFTER rasterisation (see the module note above).
    Unknown ding codes are left untouched; marks are rewritten as usages only
    (definition sites are skipped via lookbehind)."""
    tex = _DING_RE.sub(lambda m: _PIFONT_DING_GLYPH.get(m.group(1), m.group(0)), tex)
    return _SYMBOL_MACRO_RE.sub(lambda m: _SYMBOL_MACRO_GLYPH[m.group(1)], tex)


# A paper's zero-arg text macros — model/method NAMES especially
# (`\newcommand{\molmot}{\textsc{Molmo2}\xspace}`, arXiv:2605.02881) — live in
# the PREAMBLE, but the render tex pandoc reads is body-only (the preamble is
# extracted separately, §paper_pipeline), so pandoc never sees the definition and
# DROPS every usage: the name vanishes from the rendered HTML (\molmot used 62x ->
# gone). Prepending the raw preamble makes pandoc CHOKE (its \usepackage/\def
# constructs), so instead we EXPAND the simple zero-arg macros ourselves — parse
# them from the preamble and substitute their usages — so pandoc renders the
# expansion (\textsc{Molmo2} -> small-caps "Molmo2"; an unknown trailing \xspace
# is harmlessly dropped INLINE, never choking the whole doc).
_NEWCOMMAND_HEAD_RE = re.compile(
    r"\\(?:newcommand|providecommand)\*?\s*\{?\\([A-Za-z]+)\}?\s*(?=\{)"
)
_MACRO_TOKEN_RE = re.compile(r"\\([A-Za-z]+)(?![A-Za-z])")
_MACRO_EXPAND_PASSES = 6


def _parse_zero_arg_macros(preamble: str) -> dict[str, str]:
    r"""Map each ``\newcommand{\NAME}{BODY}`` / ``\providecommand`` (zero-arg
    ONLY — a following ``[n]`` means it takes arguments, skip it) to ``BODY``.
    Comment-aware; ``\renewcommand``/``\def`` excluded (they often redefine
    STANDARD commands, which we must not expand)."""
    macros: dict[str, str] = {}
    for m in _NEWCOMMAND_HEAD_RE.finditer(preamble):
        if _is_commented(preamble, m.start()):
            continue
        brace = preamble.find("{", m.end() - 1)
        if brace == -1:
            continue
        end = _skip_group(preamble, brace, "{", "}")  # index just past the '}'
        if end > brace:
            macros[m.group(1)] = preamble[brace + 1 : end - 1]
    return macros


# etoolbox toggles (`\newtoggle{arxiv}\toggletrue{arxiv}` in the preamble,
# `\iftoggle{arxiv}{A}{B}` in the body). pandoc knows \iftoggle but insists on
# a BRACED false branch, while TeX takes the next single token when it is
# unbraced: arXiv:2312.00752 (Mamba) writes `\iftoggle{arxiv}{~\parencite{x}}.`
# and pandoc aborts on the `.`, dropping the whole paper to the raw-LaTeX
# fallback. So we resolve every toggle the preamble declares, TeX-style.
_TOGGLE_DECL_RE = re.compile(r"\\(newtoggle|toggletrue|togglefalse)\s*\{([^{}]+)\}")
_TOGGLE_USE_RE = re.compile(r"\\(iftoggle|nottoggle)\s*\{([^{}]+)\}")
_CS_TOKEN_RE = re.compile(r"\\(?:[A-Za-z]+|.)", re.DOTALL)


def _parse_toggles(preamble: str) -> dict[str, bool]:
    """Final value of each etoolbox toggle declared (comment-aware)."""
    toggles: dict[str, bool] = {}
    for m in _TOGGLE_DECL_RE.finditer(preamble):
        if _is_commented(preamble, m.start()):
            continue
        name = m.group(2).strip()
        if m.group(1) == "newtoggle":
            toggles.setdefault(name, False)
        else:
            toggles[name] = m.group(1) == "toggletrue"
    return toggles


def _tex_argument(text: str, i: int) -> tuple[str, int] | None:
    """The undelimited TeX argument at ``text[i:]`` (leading spaces skipped):
    a braced group's content, else the next single token. ``(content, end)``,
    or None at end of text / an unbalanced group."""
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if i >= len(text):
        return None
    if text[i] == "{":
        end = _skip_group(text, i, "{", "}")
        if end <= i or text[end - 1] != "}":
            return None
        return text[i + 1 : end - 1], end
    m = _CS_TOKEN_RE.match(text, i)
    end = m.end() if m else i + 1
    return text[i:end], end


def resolve_toggles(tex: str, preamble: str) -> str:
    r"""Replace each ``\iftoggle{t}{A}{B}`` / ``\nottoggle{t}{A}{B}`` whose
    toggle ``t`` the preamble declares with the branch TeX would take. Unknown
    toggles and commented uses are left alone; nested toggles resolve too."""
    toggles = _parse_toggles(preamble)
    if not toggles:
        return tex
    pos = 0
    while (m := _TOGGLE_USE_RE.search(tex, pos)) is not None:
        name = m.group(2).strip()
        if name not in toggles or _is_commented(tex, m.start()):
            pos = m.end()
            continue
        first = _tex_argument(tex, m.end())
        second = _tex_argument(tex, first[1]) if first else None
        if first is None or second is None:
            pos = m.end()
            continue
        take_first = toggles[name] == (m.group(1) == "iftoggle")
        chosen = first[0] if take_first else second[0]
        tex = tex[: m.start()] + chosen + tex[second[1] :]
        pos = m.start()  # re-scan the chosen branch for nested toggles
    return tex


# `\twocolumn[<title block>]` (REVTeX / ICML): pandoc parses the optional
# argument and DROPS it, losing the title, authors and abstract
# (arXiv:1203.3835). Unwrap it so pandoc renders the block in place.
_TWOCOLUMN_RE = re.compile(r"\\twocolumn(?![A-Za-z])\s*(?=\[)")


def unwrap_twocolumn(tex: str) -> str:
    r"""Replace each uncommented ``\twocolumn[X]`` with ``X``."""
    pos = 0
    while (m := _TWOCOLUMN_RE.search(tex, pos)) is not None:
        end = optional_arg_end(tex, m.end())
        if end == m.end() or _is_commented(tex, m.start()):
            pos = m.end()
            continue
        tex = tex[: m.start()] + tex[m.end() + 1 : end - 1] + tex[end:]
        pos = m.start()
    return tex


# Argument-taking preamble macros. pandoc drops an unknown command TOGETHER
# with its braced arguments, and the render tex is body-only, so every use of a
# preamble `\newcommand{\para}[1]{..}` vanished from the HTML: Mamba
# (arXiv:2312.00752) lost all 15 `\para{Selection Mechanism.}` lead-ins, other
# papers their `\Eqn{..}` / `\secref{..}` references. We expand them the way TeX
# does. Definitions are read in preamble order with TeX's semantics
# (\newcommand never overrides, \renewcommand / \def / \DeclareRobustCommand
# do, \providecommand only fills a gap) and through the preamble's literal
# conditionals (\iftrue, \iffalse, \ifodd N, \if XY, \newif flags), so an
# author-note switch resolves to what the PDF shows: an empty body hides the
# note, a coloured body prints it. A definition under a conditional we cannot
# evaluate (\ifdefined, \ifx, ...) is never guessed: that macro is left as an
# unknown command, which pandoc drops exactly as before. Inside a macro BODY,
# `\ifx\A\B` on two parameterless macros of known meaning is resolved too.
# Anything we cannot parse or expand safely is left untouched.
_ARG_DEF_CMDS = frozenset(
    {"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "def", "gdef"}
)
_LET_CMDS = frozenset({"let", "LetLtxMacro", "NewCommandCopy", "RenewCommandCopy"})
# Primitive (\fi-terminated) conditionals whose outcome we cannot evaluate.
# etoolbox / ifthen macros (\iftoggle, \ifthenelse, \ifdef, ...) take braced
# branches and no \fi, so they are NOT conditionals for this scan.
_UNKNOWN_IFS = frozenset(
    {
        "ifx", "ifnum", "ifdim", "ifcase", "ifcat", "ifdefined", "ifcsname",
        "ifincsname", "iffontchar", "ifmmode", "ifvmode", "ifhmode", "ifinner",
        "ifvoid", "ifhbox", "ifvbox", "ifeof", "ifpdf", "ifxetex", "ifluatex",
        "ifpdftex", "ifPDFTeX", "ifXeTeX", "ifLuaTeX", "ifcsvoid",
    }
)
_BODY_COND_WORDS = _UNKNOWN_IFS | {"if", "iftrue", "iffalse", "ifodd", "else", "fi", "or"}
# A body using any of these is TeX programming, not text: never expand it.
_UNSAFE_BODY_CS = frozenset(
    {
        *_ARG_DEF_CMDS, *_LET_CMDS, *_UNKNOWN_IFS, "edef", "xdef", "if",
        "iftrue", "iffalse", "ifodd", "fi", "else", "or", "csname",
        "endcsname", "expandafter", "noexpand", "futurelet", "afterassignment",
        "makeatletter", "makeatother", "newif", "global", "long",
    }
)
_PREAMBLE_CS_RE = re.compile(r"\\(?:[A-Za-z@]+|.)", re.DOTALL)
# `@` names (under \makeatletter) are parsed so their bodies are skipped as a
# unit — a `\if` inside one is not a preamble-level conditional — but are
# never expanded.
_CS_NAME_RE = re.compile(r"[A-Za-z@]+")
# `\def\name#1#2{` — undelimited parameters only (a space or any other token
# before the `{` would make the last parameter delimited).
_DEF_PARAMS_RE = re.compile(r"\s*((?:#\d)*)\{")
_BODY_PARAM_RE = re.compile(r"#(.)", re.DOTALL)
_SENTINEL_RE = re.compile(r"PHCHUNKANCHOR\d+END")
_VERBATIM_RE = re.compile(
    r"\\begin\{(verbatim|Verbatim|lstlisting|minted|comment)(\*?)\}.*?\\end\{\1\2\}"
    r"|\\verb\*?([^A-Za-z\s])[^\n]*?\3",
    re.DOTALL,
)
_IFX_PAIR_RE = re.compile(r"\s*\\([A-Za-z]+)\s*\\([A-Za-z]+)")
_CW_BLANKS_RE = re.compile(r"^[ \t]*(?:\n[ \t]*)?")
_IFODD_ARG_RE = re.compile(r"\s*(\d+)")
_IF_CHARS_RE = re.compile(r"\s*([^\\\s{}%])\s*([^\\\s{}%])")
_TRAILING_CW_RE = re.compile(r"\\[A-Za-z]+$")
_SCAN_RE = re.compile(r"%|\\(?:[A-Za-z]+|.)", re.DOTALL)
_ARG_EXPAND_MAX_DEPTH = 8
_ARG_EXPAND_MAX_CALLS = 20_000


class _ArgMacro:
    __slots__ = ("nargs", "default", "body", "long")

    def __init__(
        self, nargs: int, default: str | None, body: str, *, long: bool = False
    ) -> None:
        self.nargs = nargs
        self.default = default
        self.body = body
        self.long = long  # \newcommand (unstarred) makes a \long macro


def _resolve_body_ifx(body: str, meanings: dict[str, tuple[str, bool]]) -> str | None:
    r"""Resolve every ``\ifx\A\B <yes>\else <no>\fi`` in a macro body whose
    ``\A`` and ``\B`` are parameterless macros of known final meaning
    (arXiv:2510.07077's ``\switchlanguage`` tests ``\ifx\paperlanguage\empty``).
    None when the body holds any other conditional or an undecidable one."""
    while True:
        toks = [
            (m.start(), m.end(), m.group()[1:])
            for m in _PREAMBLE_CS_RE.finditer(body)
            if m.group()[1:] in _BODY_COND_WORDS
        ]
        if not toks:
            return body
        start, end, word = toks[0]
        pair = _IFX_PAIR_RE.match(body, end) if word == "ifx" else None
        if pair is None or pair.group(1) not in meanings or pair.group(2) not in meanings:
            return None
        depth, else_at, fi_at = 0, (-1, -1), (-1, -1)
        for s, e, w in toks[1:]:
            if s < pair.end():
                continue
            if w not in ("else", "fi", "or"):
                depth += 1
            elif w == "else" and depth == 0 and else_at[0] == -1:
                else_at = (s, e)
            elif w == "fi":
                if depth == 0:
                    fi_at = (s, e)
                    break
                depth -= 1
        if fi_at[0] == -1:
            return None
        if meanings[pair.group(1)] == meanings[pair.group(2)]:
            branch = body[pair.end() : fi_at[0] if else_at[0] == -1 else else_at[0]]
        else:
            branch = "" if else_at[0] == -1 else body[else_at[1] : fi_at[0]]
        # TeX skips the blanks (and one line end) after each control word.
        body = (
            body[:start]
            + _CW_BLANKS_RE.sub("", branch, count=1)
            + _CW_BLANKS_RE.sub("", body[fi_at[1] :], count=1)
        )


class _ExpansionLimitError(Exception):
    """Recursion / size cap hit: the caller leaves that use untouched."""


def _strip_tex_comments(text: str) -> str:
    r"""Drop ``%`` comments TeX-style: the comment, its newline and the next
    line's leading blanks. ``\%`` is kept."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            out.append(text[i : i + 2])
            i += 2
        elif c == "%":
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            while i < n and text[i] in " \t":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _brace_group_end(text: str, i: int) -> int:
    """Index just past the balanced ``{..}`` group at ``text[i]`` (escape- and
    comment-aware), or -1 when it never closes."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            nl = text.find("\n", i)
            if nl == -1:
                return -1
            i = nl
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _skip_blanks(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\n":
        i += 1
    return i


def _read_def_name(text: str, i: int) -> tuple[str, int] | None:
    r"""The ``\name`` (bare or braced) a definition command defines."""
    i = _skip_blanks(text, i)
    braced = i < len(text) and text[i] == "{"
    if braced:
        i = _skip_blanks(text, i + 1)
    m = _PREAMBLE_CS_RE.match(text, i)
    if m is None or not _CS_NAME_RE.fullmatch(m.group()[1:]):
        return None
    i = m.end()
    if braced:
        i = _skip_blanks(text, i)
        if i >= len(text) or text[i] != "}":
            return None
        i += 1
    return m.group()[1:], i


def _parse_definition(
    text: str, cmd: str, i: int
) -> tuple[str, _ArgMacro | None, int] | None:
    r"""Parse the definition starting after the ``\cmd`` token at ``i``.

    Returns ``(name, macro, end)``; ``macro`` is None when the form is one we do
    not expand (a delimited ``\def`` parameter text, a malformed ``[n]``).
    Returns None when not even the name and body can be found."""
    long = cmd not in ("def", "gdef")
    if long:
        j = _skip_blanks(text, i)
        if j < len(text) and text[j] == "*":
            i = j + 1
            long = False
    named = _read_def_name(text, i)
    if named is None:
        return None
    name, i = named
    nargs = 0
    default: str | None = None
    supported = True
    if cmd in ("def", "gdef"):
        brace = text.find("{", i)
        if brace == -1:
            return None
        m = _DEF_PARAMS_RE.match(text, i)
        if m is not None and m.end() - 1 == brace:
            params = m.group(1)
            nargs = len(params) // 2
            supported = params == "".join(f"#{k}" for k in range(1, nargs + 1))
        else:
            supported = False
        i = brace
    else:
        j = _skip_blanks(text, i)
        if j < len(text) and text[j] == "[":
            end = optional_arg_end(text, j)
            if end == j:
                return None
            count = text[j + 1 : end - 1].strip()
            supported = count.isdigit()
            nargs = int(count) if supported else 0
            i = j = end
            j = _skip_blanks(text, j)
            if j < len(text) and text[j] == "[":
                end = optional_arg_end(text, j)
                if end == j:
                    return None
                default = text[j + 1 : end - 1]
                i = end
        i = _skip_blanks(text, i)
    if i >= len(text) or text[i] != "{":
        return None
    end = _brace_group_end(text, i)
    if end == -1:
        return None
    body = text[i + 1 : end - 1]
    if not supported or not 0 <= nargs <= 9 or (default is not None and nargs == 0):
        return name, None, end
    return name, _ArgMacro(nargs, default, body, long=long), end


def _eval_literal_if(
    cs: str, text: str, i: int, flags: dict[str, bool | None]
) -> tuple[bool | None, int] | None:
    r"""Evaluate the primitive conditional ``\cs`` whose token ends at ``i``:
    ``(value, end)`` with ``value`` None when it cannot be decided, or None when
    ``\cs`` is not a ``\fi``-terminated conditional at all."""
    if cs == "iftrue":
        return True, i
    if cs == "iffalse":
        return False, i
    if cs == "ifodd":
        m = _IFODD_ARG_RE.match(text, i)
        return (int(m.group(1)) % 2 == 1, m.end()) if m else (None, i)
    if cs == "if":
        m = _IF_CHARS_RE.match(text, i)
        return (m.group(1) == m.group(2), m.end()) if m else (None, i)
    if cs.startswith("if") and cs[2:] in flags:
        return flags[cs[2:]], i
    if cs in _UNKNOWN_IFS or cs.startswith("if@"):
        return None, i
    return None


def _parse_arg_macros(preamble: str) -> dict[str, _ArgMacro]:
    r"""Map each argument-taking macro the preamble defines, in TeX's final
    state, to its definition. A name whose final definition is uncertain
    (under an undecidable conditional), unsupported, or unsafe to expand
    textually (TeX programming, ``@``-internals, self-reference, a ``\let``
    alias in its body) is left out."""
    text = _strip_tex_comments(preamble)
    defined: dict[str, _ArgMacro | None] = {}  # None: defined, but not expandable
    flags: dict[str, bool | None] = {}
    aliases: set[str] = set()
    stack: list[bool | None] = []  # the branch value currently being read
    events: list[tuple[str, str, _ArgMacro | None, int, bool | None]] = []
    first_if = -1
    broken = False

    def region() -> bool | None:
        if any(v is False for v in stack):
            return False
        return None if any(v is None for v in stack) else True

    i = 0
    while (m := _PREAMBLE_CS_RE.search(text, i)) is not None:
        cs = m.group()[1:]
        i = m.end()
        if cs in _ARG_DEF_CMDS:
            parsed = _parse_definition(text, cs, i)
            if parsed is None:
                continue
            name, macro, i = parsed
            events.append((cs, name, macro, m.start(), region()))
        elif cs in _LET_CMDS:
            named = _read_def_name(text, i)
            if named is not None:
                aliases.add(named[0])
                events.append(("let", named[0], None, m.start(), region()))
        elif cs == "newif":
            named = _read_def_name(text, i)
            if named is not None and named[0].startswith("if"):
                flag = named[0][2:]
                state = region()
                if state is not False:
                    flags[flag] = False if state else None
                i = named[1]
        elif cs.endswith(("true", "false")) and (
            (flag := cs.removesuffix("true").removesuffix("false")) in flags
        ):
            state = region()
            if state is not False:
                flags[flag] = cs.endswith("true") if state else None
        elif cs == "else":
            if stack:
                stack[-1] = None if stack[-1] is None else not stack[-1]
        elif cs == "fi":
            if stack:
                stack.pop()
            else:
                broken = True
        elif (cond := _eval_literal_if(cs, text, i, flags)) is not None:
            if first_if == -1:
                first_if = m.start()
            stack.append(cond[0])
            i = cond[1]
    if stack:
        broken = True

    poisoned: set[str] = set()
    for cmd, name, macro, pos, state in events:
        if broken and first_if != -1 and pos > first_if:
            state = None  # the conditional structure is not trustworthy
        if state is False:
            continue
        if state is None:
            poisoned.add(name)
            defined.pop(name, None)
            continue
        if cmd in ("newcommand", "providecommand") and (
            name in defined or name in poisoned
        ):
            continue  # TeX keeps the existing definition
        poisoned.discard(name)
        defined[name] = None if cmd == "let" else macro

    # Final meanings of parameterless macros (+ the kernel's \empty), for \ifx.
    meanings = {"empty": ("", False)} | {
        n: (m.body, m.long)
        for n, m in defined.items()
        if m is not None and m.nargs == 0 and n not in poisoned
    }
    out: dict[str, _ArgMacro] = {}
    for name, macro in defined.items():
        if macro is None or macro.nargs == 0 or name in poisoned:
            continue
        resolved = _resolve_body_ifx(macro.body, meanings)
        if resolved is None:
            continue
        macro.body = body = resolved
        if "@" in body or "@" in name:
            continue
        params_ok = all(
            p.group(1).isdigit() and 1 <= int(p.group(1)) <= macro.nargs
            for p in _BODY_PARAM_RE.finditer(body)
        ) and not body.rstrip().endswith("#")
        if not params_ok:
            continue
        words = {t[1:] for t in _PREAMBLE_CS_RE.findall(body) if t[1:].isalpha()}
        if words & _UNSAFE_BODY_CS or name in words or words & aliases:
            continue
        if any(
            (w.startswith("if") and w[2:] in flags)
            or (w.endswith("true") and w[:-4] in flags)
            or (w.endswith("false") and w[:-5] in flags)
            for w in words
        ):
            continue  # reads or sets a \newif flag
        out[name] = macro
    return out


_BODY_DEF_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|def|gdef"
    r"|edef|xdef|let|LetLtxMacro)\*?\s*\{?\s*\\([A-Za-z]+)"
)


def _protected_spans(tex: str) -> list[tuple[int, int]]:
    """Math and verbatim regions, where a macro is left for MathJax / as code."""
    spans = find_math_spans(tex) + [(m.start(), m.end()) for m in _VERBATIM_RE.finditer(tex)]
    return sorted(spans)


def _read_call_argument(tex: str, i: int, found: list[str]) -> tuple[str, int] | None:
    """One undelimited argument after ``i``: blanks, comments and chunk
    sentinels (collected into ``found``) are skipped; a blank line (``\\par``)
    or a closing brace ends the search. ``(content, end)`` or None."""
    n = len(tex)
    newlines = 0
    while i < n:
        c = tex[i]
        if c in " \t\r":
            i += 1
        elif c == "\n":
            newlines += 1
            if newlines > 1:
                return None
            i += 1
        elif c == "%":
            nl = tex.find("\n", i)
            if nl == -1:
                return None
            i = nl + 1
        elif (s := _SENTINEL_RE.match(tex, i)) is not None:
            found.append(s.group())
            i = s.end()
        else:
            break
    if i >= n or tex[i] in "}$&#^_":
        return None
    if tex[i] == "{":
        end = _brace_group_end(tex, i)
        return None if end == -1 else (tex[i + 1 : end - 1], end)
    m = _CS_TOKEN_RE.match(tex, i)
    end = m.end() if m else i + 1
    return tex[i:end], end


def _parse_call(
    tex: str, i: int, macro: _ArgMacro
) -> tuple[list[str], int, list[str]] | None:
    """The arguments of a use whose name ends at ``i``:
    ``(args, end, sentinels)``, or None when TeX's reading is not certain."""
    found: list[str] = []
    args: list[str] = []
    if macro.default is not None:
        j = i
        while j < len(tex) and tex[j] in " \t":
            j += 1
        if j < len(tex) and tex[j] == "[":
            end = optional_arg_end(tex, j)
            if end == j:
                return None
            args.append(tex[j + 1 : end - 1])
            i = end
        else:
            args.append(macro.default)
    while len(args) < macro.nargs:
        got = _read_call_argument(tex, i, found)
        if got is None:
            return None
        args.append(got[0])
        i = got[1]
    clean = []
    for a in args:
        found.extend(_SENTINEL_RE.findall(a))
        # TeX drops comments while reading the argument.
        clean.append(_strip_tex_comments(_SENTINEL_RE.sub("", a)))
    return clean, i, found


def _glues(left: str, right: str) -> bool:
    """True when ``left`` ends in a control word that would absorb the letters
    starting ``right`` (a space after a control word is invisible to TeX)."""
    return right[:1].isalpha() and _TRAILING_CW_RE.search(left[-64:]) is not None


def _join_tex(left: str, right: str) -> str:
    return left + " " + right if _glues(left, right) else left + right


def _substitute(body: str, args: list[str]) -> str:
    out = ""
    pos = 0
    for p in _BODY_PARAM_RE.finditer(body):
        out = _join_tex(out + body[pos : p.start()], args[int(p.group(1)) - 1])
        pos = p.end()
    return _join_tex(out, body[pos:])


def _expand_arg_uses(
    tex: str, macros: dict[str, _ArgMacro], depth: int, calls: list[int]
) -> str:
    if depth > _ARG_EXPAND_MAX_DEPTH:
        raise _ExpansionLimitError
    spans = _protected_spans(tex)
    si = 0
    parts: list[str] = []
    i = 0
    while (m := _SCAN_RE.search(tex, i)) is not None:
        while si < len(spans) and spans[si][0] < i:
            si += 1
        if si < len(spans) and spans[si][0] <= m.start():
            parts.append(tex[i : spans[si][1]])
            i = spans[si][1]
            continue
        if m.group() == "%":
            nl = tex.find("\n", m.start())
            end = len(tex) if nl == -1 else nl
            parts.append(tex[i:end])
            i = end
            continue
        macro = macros.get(m.group()[1:])
        call = _parse_call(tex, m.end(), macro) if macro is not None else None
        if macro is None or call is None:
            parts.append(tex[i : m.end()])
            i = m.end()
            continue
        args, end, sentinels = call
        calls[0] += 1
        if calls[0] > _ARG_EXPAND_MAX_CALLS:
            raise _ExpansionLimitError
        try:
            expansion = _expand_arg_uses(
                _substitute(macro.body, args), macros, depth + 1, calls
            )
        except _ExpansionLimitError:
            if depth > 0:
                raise
            parts.append(tex[i:end])  # cannot be expanded safely: keep the use
            i = end
            continue
        parts.append(tex[i : m.start()])
        # No control word may absorb the letters after it — including the
        # source text that follows the use.
        for piece in ("".join(sentinels), expansion, tex[end : end + 1]):
            if _glues("".join(parts[-3:])[-64:], piece):
                parts.append(" ")
            parts.append(piece)
        parts.pop()  # the lookahead character itself is re-read by the scan
        i = end
    return "".join(parts) + tex[i:]


def expand_arg_macros(tex: str, preamble: str) -> str:
    r"""Expand every use of the preamble's argument-taking macros in ``tex``
    (see the block comment above). Uses inside comments, math (left to
    MathJax's macro table) and verbatim are untouched, as are names the body
    itself (re)defines; chunk sentinels in a consumed argument are kept once,
    just before the expansion. A no-op when the preamble defines none."""
    macros = _parse_arg_macros(preamble)
    if not macros:
        return tex
    for name in _BODY_DEF_RE.findall(tex):
        macros.pop(name, None)
    if not macros:
        return tex
    try:
        return _expand_arg_uses(tex, macros, 0, [0])
    except _ExpansionLimitError:
        return tex


def expand_preamble_macros(tex: str, preamble: str) -> str:
    r"""Expand the paper's zero-arg ``\newcommand`` text macros (parsed from
    ``preamble``) in ``tex`` so pandoc renders their content instead of dropping
    the unknown macro. Multi-pass (bounded) for nested macros; no-op when the
    preamble defines none. etoolbox toggles are resolved first
    (:func:`resolve_toggles`) and ``\twocolumn[...]`` title blocks unwrapped
    (:func:`unwrap_twocolumn`); argument-taking macros are expanded after the
    zero-arg ones (:func:`expand_arg_macros`)."""
    tex = unwrap_twocolumn(resolve_toggles(tex, preamble))
    macros = _parse_zero_arg_macros(preamble)

    def _sub(m: re.Match[str]) -> str:
        return macros.get(m.group(1), m.group(0))

    def _expand_zero_arg(text: str) -> str:
        for _ in range(_MACRO_EXPAND_PASSES):
            new = _MACRO_TOKEN_RE.sub(_sub, text)
            if new == text:
                break
            text = new
        return text

    if macros:
        tex = _expand_zero_arg(tex)
    expanded = expand_arg_macros(tex, preamble)
    if expanded != tex:
        # Bodies may hold toggles (Mamba's \para) and zero-arg macros.
        tex = resolve_toggles(expanded, preamble)
        if macros:
            tex = _expand_zero_arg(tex)
    return tex


def render_html(
    *,
    source: Path,
    kind: Literal["latex", "pdf"],
    out_path: Path,
    resource_dir: Path | None = None,
    macros: dict[str, MacroValue] | None = None,
) -> Path:
    """Render ``source`` to an HTML artefact at ``out_path``.

    ``resource_dir`` (latex only) is where figures referenced by the flattened
    source actually live — typically the extracted ``source/`` subtree, a
    different directory from the flattened ``.tex``. pandoc searches it via
    ``--resource-path``; figure ``<img>`` refs are then rewritten by
    ``_externalize_local_images`` to relative ``asset/`` URLs the Citation
    Canvas iframe resolves back to the backend (serving each figure lazily as a
    file). We deliberately do NOT base64-inline figures: a paper with 70MB of
    figures produced a 70MB HTML that OOM'd the iframe (arxiv:2605.02881). Math
    is rendered via an EXTERNAL MathJax CDN ``<script>`` (``--mathjax``, not
    ``--embed-resources``) so multi-line environments like ``\\begin{aligned}``
    render in the browser without fetching+inlining ~1.3MB of MathJax per paper.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "pdf":
        _render_pdf(source, out_path)
    elif kind == "latex":
        if shutil.which("pandoc"):
            try:
                _render_latex_pandoc(source, out_path, resource_dir=resource_dir)
                if resource_dir is not None:
                    _externalize_local_images(out_path, resource_dir)
                # Feed MathJax the paper's macro definitions + curated package
                # macros so \vx, \Ls, \mathbbm, … render instead of leaking raw.
                _inject_mathjax_macros(out_path, macros)
                return out_path
            except subprocess.CalledProcessError as exc:
                # Idiosyncratic LaTeX commonly trips pandoc with non-zero exit.
                # Fall back to pylatexenc so the upstream pipeline (which has
                # already spent significant work on download + extract) still
                # produces a usable HTML artefact for the Citation Canvas.
                logger.warning(
                    "pandoc failed on %s (exit %s); falling back to pylatexenc. "
                    "stderr: %s",
                    source,
                    exc.returncode,
                    (exc.stderr or "")[:500],
                )
                # A stray unclosed brace (author typo pdflatex tolerates) makes
                # pandoc reject the whole document. Re-balance and retry once
                # before degrading to the plain-text fallback.
                if _try_pandoc_brace_balanced(
                    source, out_path, resource_dir=resource_dir, macros=macros,
                ):
                    return out_path
            except subprocess.TimeoutExpired:
                # pandoc hung past the cap — kill it and fall back rather than
                # parking the ingest until the worker OOMs.
                logger.warning(
                    "pandoc timed out after %ss on %s; falling back to pylatexenc.",
                    _PANDOC_TIMEOUT_SECONDS,
                    source,
                )
        # pandoc absent OR exited non-zero — try pylatexenc.
        try:
            _render_latex_pylatexenc(source, out_path)
            return out_path
        except Exception as exc:  # noqa: BLE001 — pylatexenc raises bare Exception subclasses on hostile LaTeX
            logger.warning(
                "pylatexenc failed on %s (%s: %s); falling back to raw-text envelope.",
                source,
                type(exc).__name__,
                str(exc)[:200],
            )
            _render_latex_raw_envelope(source, out_path)
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    return out_path


def _render_pdf(pdf_path: Path, out_path: Path) -> None:
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        pieces = ["<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"]
        for page in doc:
            pieces.append("<div class='page'>")
            pieces.append(page.get_text("html"))
            pieces.append("</div>")
        pieces.append("</body></html>")
    # PyMuPDF inlines every page image as a base64 data: URI — the PDF-render
    # counterpart of the figure-bloat that OOM'd the canvas. Extract each to a
    # file and rewrite to a relative asset/ URL served lazily by the backend.
    html = _externalize_data_uri_images(
        "".join(pieces),
        out_dir=out_path.parent / "pdf_assets",
        html_dir=out_path.parent,
    )
    out_path.write_text(html, encoding="utf-8")


def _render_latex_pandoc(
    tex_path: Path, out_path: Path, *, resource_dir: Path | None = None,
) -> None:
    cmd = [
        "pandoc",
        "--from", "latex",
        "--to", "html5",
        "--standalone",
        # Render math via an external MathJax CDN <script>. pandoc's built-in
        # conversion can't handle multi-line math (\begin{aligned}, $$..$$) and
        # dumps raw TeX; MathJax renders it in the browser. We deliberately do
        # NOT use --embed-resources: it would fetch + inline ~1.3MB of MathJax
        # into every paper at ingest (~12s/paper + network dependency). Figure
        # <img> refs are rewritten to served asset/ URLs by
        # _externalize_local_images (NOT base64-inlined — that OOM'd the canvas).
        "--mathjax",
        # Preserve the source's line breaks instead of reflowing at ~72 cols.
        # Default wrapping splits a long line inside math, and a `%` LaTeX
        # comment line (e.g. arXiv:1706.03762's commented MultiHead `where`
        # row) then only comments its FIRST wrapped fragment — the remainder
        # (here an invalid double-subscript `QW_Q_i`) becomes live math and
        # breaks the render. Preserving line breaks keeps each `%` comment on
        # its own line, fully commented.
        "--wrap=preserve",
    ]
    if resource_dir is not None:
        # Figures live in the extracted source/ subtree, not next to the
        # flattened .tex — tell pandoc where to find them for embedding.
        cmd += ["--resource-path", str(resource_dir)]
    cmd += [str(tex_path), "-o", str(out_path)]
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        cwd=tex_path.parent,
        timeout=_PANDOC_TIMEOUT_SECONDS,
    )


def _try_pandoc_brace_balanced(
    source: Path,
    out_path: Path,
    *,
    resource_dir: Path | None,
    macros: dict[str, MacroValue] | None,
) -> bool:
    r"""Retry pandoc once after deleting constructs pandoc rejects but pdflatex
    tolerates: stray unclosed ``{`` braces, orphaned ``\end{X}`` (whose
    ``\begin`` is commented out / missing), AND pandoc-hostile definition
    commands (``\newcolumntype`` / the ``\newtcolorbox`` family) whose ``#1``
    aborts the parse.

    Returns True iff the repaired source rendered. The temp copy lives beside
    ``source`` so its ``\input`` + relative figure paths still resolve; sentinels
    are preserved (we only delete the offending spans), so chunk anchoring
    survives the retry.
    """
    text = source.read_text(encoding="utf-8", errors="ignore")
    brace_positions = _unmatched_open_braces(text)
    end_spans = _orphaned_env_ends(text)
    def_spans = _pandoc_hostile_def_spans(text)
    if (
        len(brace_positions) > _MAX_BRACE_FIX
        or len(end_spans) > _MAX_ENV_FIX
        or len(def_spans) > _MAX_DEF_FIX
    ):
        return False
    if not brace_positions and not end_spans and not def_spans:
        return False
    # Collect every cut as a (start, end) span (braces are one char; orphaned
    # \end are a token; hostile defs a multi-line block), MERGE overlaps so a
    # nested cut can't double-delete, then delete in descending order so earlier
    # indices stay valid.
    raw_cuts = sorted(
        [(p, p + 1) for p in brace_positions] + end_spans + def_spans
    )
    cuts: list[tuple[int, int]] = []
    for s, e in raw_cuts:
        if cuts and s <= cuts[-1][1]:
            cuts[-1] = (cuts[-1][0], max(cuts[-1][1], e))
        else:
            cuts.append((s, e))
    chars = list(text)
    for s, e in sorted(cuts, key=lambda x: x[0], reverse=True):
        del chars[s:e]
    repaired = "".join(chars)
    balanced = source.with_name(source.stem + ".balanced.tex")
    try:
        balanced.write_text(repaired, encoding="utf-8")
        _render_latex_pandoc(balanced, out_path, resource_dir=resource_dir)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    finally:
        balanced.unlink(missing_ok=True)
    if resource_dir is not None:
        _externalize_local_images(out_path, resource_dir)
    _inject_mathjax_macros(out_path, macros)
    logger.info(
        "pandoc succeeded on %s after removing %d stray brace(s) + %d orphaned "
        "end(s) + %d hostile def(s)",
        source, len(brace_positions), len(end_spans), len(def_spans),
    )
    return True


def _inject_mathjax_macros(
    html_path: Path, macros: dict[str, MacroValue] | None,
) -> None:
    """Splice a ``window.MathJax`` macro config into the rendered HTML, just
    before pandoc's MathJax loader ``<script>``.

    Always injects the curated package macros (so ``\\mathbbm`` etc. render even
    for papers with no custom preamble); ``macros`` adds the paper's own author
    macros on top. No-op when the HTML has no MathJax loader (e.g. a render with
    no math, or a non-pandoc fallback path)."""
    html = html_path.read_text(encoding="utf-8")
    m = _MATHJAX_SCRIPT_RE.search(html)
    if m is None:
        return
    config = build_mathjax_config_script(macros)
    new_html = html[: m.start()] + config + "\n  " + html[m.start() :]
    html_path.write_text(new_html, encoding="utf-8")


def _externalize_local_images(html_path: Path, resource_dir: Path) -> None:
    """Rewrite ``<img src="rel/path">`` referencing local raster files under
    ``resource_dir`` into a relative ``asset/<path>`` URL, where ``<path>`` is
    the figure's location relative to the HTML file's own directory.

    The Citation Canvas loads ``source.html`` via a backend URL
    (``/papers/content/{id}/html``), so a relative ``asset/`` src resolves to
    ``/papers/content/{id}/asset/<path>`` — served lazily as a file by
    ``serve_asset``. We deliberately do NOT base64-inline figures: a paper with
    70MB of figures produced a 70MB HTML that OOM'd the iframe (arxiv:2605.02881).

    Remote (http/https), data:, and already-rewritten (asset/) srcs are left
    untouched; so are missing or non-raster files."""
    html = html_path.read_text(encoding="utf-8")
    base_dir = html_path.parent.resolve()

    def _sub(m: re.Match[str]) -> str:
        pre, src, post = m.group(1), m.group(2), m.group(3)
        if src.startswith(("data:", "http://", "https://", "//", "asset/")):
            return m.group(0)
        figure = resource_dir / src
        if figure.suffix.lower() not in _IMG_MIME or not figure.is_file():
            return m.group(0)
        try:
            rel = figure.resolve().relative_to(base_dir)
        except ValueError:
            # Figure lives outside the served HTML's directory — can't form a
            # relative asset URL the iframe would resolve. Leave it as-is.
            return m.group(0)
        return f"{pre}asset/{rel.as_posix()}{post}"

    new_html = _IMG_SRC_RE.sub(_sub, html)
    if new_html != html:
        html_path.write_text(new_html, encoding="utf-8")


def _externalize_data_uri_images(html: str, *, out_dir: Path, html_dir: Path) -> str:
    """Extract inline ``data:image/...;base64,...`` ``<img>`` srcs to files under
    ``out_dir`` and rewrite each to a relative ``asset/<path>`` URL (relative to
    ``html_dir``, the served HTML's directory). Used for PyMuPDF's PDF-page HTML,
    which inlines every page image as a data: URI. Undecodable payloads are left
    inline (defensive — never raise mid-render)."""
    out_dir_resolved = out_dir.resolve()
    base_dir = html_dir.resolve()
    counter = 0
    made_dir = False

    def _sub(m: re.Match[str]) -> str:
        nonlocal counter, made_dir
        pre, subtype, b64, post = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        try:
            data = base64.b64decode(b64)
        except ValueError:  # binascii.Error subclasses ValueError
            return m.group(0)
        if not made_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            made_dir = True
        fname = f"img_{counter}{_DATA_URI_EXT.get(subtype, '.png')}"
        counter += 1
        (out_dir / fname).write_bytes(data)
        try:
            rel = (out_dir_resolved / fname).relative_to(base_dir)
        except ValueError:
            return m.group(0)
        return f"{pre}asset/{rel.as_posix()}{post}"

    return _DATA_URI_IMG_RE.sub(_sub, html)


def _render_latex_pylatexenc(tex_path: Path, out_path: Path) -> None:
    text = LatexNodes2Text().latex_to_text(
        tex_path.read_text(encoding="utf-8", errors="ignore"),
    )
    # Minimal HTML envelope so the canvas can scroll-into-view by char offsets.
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = "<pre style='white-space:pre-wrap'>" + escaped + "</pre>"
    out_path.write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{body}</body></html>",
        encoding="utf-8",
    )


def _render_latex_raw_envelope(tex_path: Path, out_path: Path) -> None:
    """Last-resort HTML envelope: HTML-escape the raw .tex bytes inside <pre>.

    Used when both pandoc and pylatexenc fail to parse the source. Citation
    Canvas chunk navigation still works because chunk offsets are computed
    against the flattened LaTeX body, not this HTML view.
    """
    import html

    raw = tex_path.read_bytes().decode("utf-8", errors="replace")
    body = "<pre style='white-space:pre-wrap'>" + html.escape(raw) + "</pre>"
    out_path.write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{body}</body></html>",
        encoding="utf-8",
    )
