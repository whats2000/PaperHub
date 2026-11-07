r"""Section-name normalisation shared by every chunking path.

Section names reach ``list_sections`` / ``read_section`` (paper_qa), ideation
cards, slide grounding and the Citation Canvas, so they must read as headings.
Two sources produced junk:

* **LaTeX** — the chunker read ``\section{...}`` up to the FIRST ``}``, so a
  heading built from a custom macro surfaced as ``\molmoactt`` or
  ``\modelname{``. :func:`clean_latex_heading` takes the brace-balanced
  argument, expands the paper's own macros and strips formatting commands.
* **PDF** — a line set in a larger font is not always a heading: inline code
  spans, code listings and wrapped prose lines also qualify by font size.
  :func:`is_heading_shaped` rejects text that cannot be a heading.

Both are pure functions over strings.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from paperhub.pipelines.mathjax_macros import MacroValue, _read_braced

__all__ = [
    "clean_latex_heading",
    "is_heading_shaped",
    "is_wrapped_continuation",
    "read_section_argument",
]

# Commands whose (single) braced argument is metadata, not heading text.
_DROP_WITH_ARG = frozenset({
    "label", "ref", "eqref", "autoref", "cref", "Cref", "pageref",
    "cite", "citep", "citet", "citealp", "citeauthor", "citeyear",
    "footnote", "footnotemark", "footnotetext", "thanks", "index",
    "vspace", "hspace", "phantom", "hphantom", "vphantom", "includegraphics",
})
# Control symbols that stand for a literal character.
_LITERAL_SYMBOLS = {"&": "&", "%": "%", "_": "_", "#": "#", "$": "$", "{": "{", "}": "}"}
_WORD_RE = re.compile(r"[A-Za-z@]+")
_ARG_REF_RE = re.compile(r"#([1-9])")
_EXPAND_PASSES = 5
_WS_RE = re.compile(r"\s+")


def read_section_argument(text: str, brace_index: int) -> str | None:
    """The brace-balanced argument opening at ``text[brace_index]`` (``{``),
    or ``None`` when the group is unbalanced."""
    inner, _ = _read_braced(text, brace_index)
    return inner


def _fill_args(template: str, args: list[str]) -> str:
    """Substitute ``#1``..``#9`` in a macro body."""
    def arg(m: re.Match[str]) -> str:
        k = int(m.group(1))
        return args[k - 1] if k <= len(args) else ""

    return _ARG_REF_RE.sub(arg, template)


def _expand_user_macros(tex: str, macros: Mapping[str, MacroValue]) -> str:
    """Expand the paper's own ``\\newcommand`` / ``\\def`` macros (bounded
    passes, so a self-referential definition cannot loop)."""
    if not macros:
        return tex
    for _ in range(_EXPAND_PASSES):
        out: list[str] = []
        i = 0
        changed = False
        while i < len(tex):
            c = tex[i]
            if c != "\\":
                out.append(c)
                i += 1
                continue
            m = _WORD_RE.match(tex, i + 1)
            if m is None:  # control symbol: copy both chars verbatim
                out.append(tex[i : i + 2])
                i += 2
                continue
            name = m.group(0)
            body = macros.get(name)
            if body is None:
                out.append(tex[i : m.end()])
                i = m.end()
                continue
            j = m.end()
            if isinstance(body, str):
                out.append(body)
            else:
                template, nargs = str(body[0]), int(str(body[1]))
                args: list[str] = []
                for _ in range(nargs):
                    while j < len(tex) and tex[j] == " ":
                        j += 1
                    arg, nj = _read_braced(tex, j)
                    if arg is None:  # bare single-token argument
                        arg, nj = (tex[j : j + 1], j + 1) if j < len(tex) else ("", j)
                    args.append(arg)
                    j = nj
                out.append(_fill_args(template, args))
            changed = True
            i = j
        tex = "".join(out)
        if not changed:
            break
    return tex


def _strip_commands(tex: str) -> str:
    """Render LaTeX heading text as plain text: unwrap formatting commands,
    drop metadata commands and bare switches, keep ``$...$`` math verbatim."""
    out: list[str] = []
    i = 0
    n = len(tex)
    while i < n:
        c = tex[i]
        if c == "$":
            end = tex.find("$", i + 1)
            if end < 0:
                out.append(tex[i:])
                break
            out.append(tex[i : end + 1])
            i = end + 1
            continue
        if c == "~":
            out.append(" ")
            i += 1
            continue
        if c in "{}":
            i += 1
            continue
        if c != "\\":
            out.append(c)
            i += 1
            continue
        m = _WORD_RE.match(tex, i + 1)
        if m is None:
            sym = tex[i + 1 : i + 2]
            if sym == "\\" or sym.isspace() or sym in {",", ";", ":", "!"}:
                out.append(" ")
            else:
                out.append(_LITERAL_SYMBOLS.get(sym, ""))
            i += 2
            continue
        name = m.group(0)
        j = m.end()
        if name == "texorpdfstring":
            first, j = _read_braced(tex, j)
            second, j2 = _read_braced(tex, j)
            if first is not None and second is not None:
                out.append(_strip_commands(second))
                i = j2
                continue
        # Optional [..] argument (e.g. \footnote[2]{..}) is never heading text.
        if j < n and tex[j] == "[":
            close = tex.find("]", j)
            if close > 0:
                j = close + 1
        arg, after = _read_braced(tex, j)
        if arg is None:
            i = j  # bare switch (\bf, \xspace, \small, ...) — drop it
            continue
        if name not in _DROP_WITH_ARG:
            out.append(_strip_commands(arg))
        i = after
    return "".join(out)


def clean_latex_heading(
    raw: str, macros: Mapping[str, MacroValue] | None = None,
) -> str:
    r"""Plain-text heading from a ``\section{...}`` argument.

    ``raw`` is the brace-balanced argument; ``macros`` the paper's own macro
    definitions (``extract_macros_from_dir``). A heading without LaTeX markup
    is returned whitespace-normalised but otherwise unchanged. Falls back to
    ``raw.strip()`` when cleaning would leave nothing.
    """
    stripped = raw.strip()
    if not any(ch in stripped for ch in "\\{}~$"):
        return _WS_RE.sub(" ", stripped)
    text = _strip_commands(_expand_user_macros(stripped, macros or {}))
    text = _WS_RE.sub(" ", text).strip()
    return text or stripped


# --- PDF / OCR heading shape -------------------------------------------------

_IDENT_RE = re.compile(r"[A-Za-z0-9]_[A-Za-z0-9]")
_BAD_END = ("\\", ",", ";", "-", "(", "/", "=")
_MAX_HEADING_WORDS = 16


def is_heading_shaped(name: str) -> bool:
    """True when ``name`` reads like a section heading.

    Rejects what font-size heuristics (PyMuPDF) and OCR layout models (Marker)
    misfile as headings: wrapped prose lines (lowercase or punctuation start,
    a trailing comma / hyphen / backslash, unbalanced parentheses), code
    (``snake_case`` identifiers, shell continuations, ``--flags``), chart axis
    tick runs (mostly digits), and over-long lines.
    """
    s = name.strip()
    if len(s) < 3 or not s.isprintable() or "�" in s:
        return False
    first = s[0]
    if not (first.isupper() or first.isdigit() or (first.isalpha() and not first.isascii())):
        return False
    if s.endswith(_BAD_END):
        return False
    if s.count("(") != s.count(")") or s.count("[") != s.count("]"):
        return False
    if _IDENT_RE.search(s) or " --" in s or "://" in s or "www." in s:
        return False
    if len(s.split()) > _MAX_HEADING_WORDS:
        return False
    letters = sum(ch.isalpha() for ch in s)
    return letters * 2 >= len(s.replace(" ", ""))


# A wrapped heading's second line is short ("applications").
_MAX_CONTINUATION_WORDS = 6


def is_wrapped_continuation(name: str) -> bool:
    """True for the lowercase tail of a heading that layout extraction split
    over two lines ("Literature on ... segmentation" + "applications"). The
    caller must also check the line directly follows a real heading."""
    s = name.strip()
    return (
        s[:1].islower()
        and len(s.split()) <= _MAX_CONTINUATION_WORDS
        and is_heading_shaped(s[:1].upper() + s[1:])
    )
