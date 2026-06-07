r"""Extract LaTeX macro definitions from a paper preamble and render them as a
MathJax ``tex.macros`` config for the Citation Canvas.

Why this exists: the Citation Canvas renders LaTeX papers to HTML with pandoc
``--mathjax``. ``extract_latex`` strips the preamble (where ``\newcommand``s
live) before rendering, so author macros — ``\vx``, ``\Ls``, ``\1`` from files
like Goodfellow's ubiquitous ``math_commands.tex`` — reach MathJax undefined
and render as raw text. We parse those definitions back out and hand them to
MathJax as ``tex.macros`` (which the default ``tex-chtml-full`` build reads via
its ``configmacros`` package), merged with curated mappings for package
commands MathJax lacks natively (``\mathbbm``, ``\bm``, …).

Parsing covers the dominant definition forms (``\newcommand`` with optional
arg-count + default, ``\renewcommand``/``\providecommand``,
``\DeclareMathOperator``, simple no-arg ``\def``). Anything we cannot parse is
skipped — the command then renders raw exactly as it does today (no regression).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Macro value is either a replacement string, or [replacement, nargs], or
# [replacement, nargs, optional_default] — exactly MathJax's tex.macros shape.
MacroValue = str | list[object]

# Package-level commands MathJax's default build does not ship. Mirrors the
# frontend KATEX_MACROS philosophy; author definitions (extracted) override.
CURATED_MACROS: dict[str, MacroValue] = {
    # bbm package — blackboard bold incl. the indicator \mathbbm{1}.
    "mathbbm": [r"\mathbb{#1}", 1],
    # bm package — bold math; MathJax ships \boldsymbol but not \bm.
    "bm": [r"\boldsymbol{#1}", 1],
    # isomath — slanted sans; closest native fallback.
    "mathsfit": [r"\mathit{#1}", 1],
    # Capitalized "wide accent" idiom some preambles use (\Tilde{X} = a tilde
    # scaled to X). MathJax ships \widetilde but not \Tilde, and papers use it
    # only INSIDE their own macros (arXiv:2406.07524: \def\Rrevt{\Tilde{R}_t}),
    # so it never reaches extract_macros and renders as "Undefined control
    # sequence \Tilde". Map to the native wide tilde.
    "Tilde": [r"\widetilde{#1}", 1],
    # LaTeX font-size switches are NOT in MathJax's default build (they live in
    # the unshipped `textmacros` extension), so a paper that de-emphasises a
    # formula annotation with `\text{\footnotesize $\because …$}`
    # (arXiv:2406.07524) hit "Undefined control sequence \footnotesize" and the
    # annotation rendered broken. Map each to a no-op so the content renders (at
    # default size) instead of erroring. KaTeX (the chat) supports these
    # natively, so this only affects the MathJax Citation-Canvas path.
    "tiny": "",
    "scriptsize": "",
    "footnotesize": "",
    "small": "",
    "normalsize": "",
    "large": "",
    "Large": "",
    "LARGE": "",
    "huge": "",
    "Huge": "",
    # Spacing commands that land inside math — authored, or pulled into a \[..\]
    # block by pandoc grouping (arXiv:2512.04952 renders `\[\vspace{-0.5em}\]`).
    # MathJax has no \vspace; map \vspace/\hspace to a no-op that consumes the
    # dimension argument and emits nothing (the bare ones are commonly no-arg).
    "vspace": ["", 1],
    "hspace": ["", 1],
    "smallskip": "",
    "medskip": "",
    "bigskip": "",
    # Cross-reference commands sometimes sit inside math (e.g. \eqref to a tag
    # within an aligned block); MathJax can't resolve them and errors. No-op
    # them (consume the {label}) so the surrounding math still renders.
    "ref": ["", 1],
    "eqref": ["", 1],
    "cref": ["", 1],
    "Cref": ["", 1],
    # mathtools assignment colon (:=) — not in MathJax's base build.
    "coloneqq": r"\mathrel{:=}",
    "eqqcolon": r"\mathrel{=:}",
    # `\dag`/`\ddag` are LaTeX TEXT-mode symbols (†/‡) that papers routinely use
    # inside math for footnote markers — `$^{\dag}$ represents the reproduced
    # result` (arXiv:2602.20200). MathJax's math build ships only the math names
    # `\dagger`/`\ddagger`, so `\dag` reached it as "Undefined control sequence"
    # and the superscript rendered broken. Map each to its math equivalent.
    "dag": r"\dagger",
    "ddag": r"\ddagger",
}

# Unescaped `%` starts a LaTeX comment to end-of-line; `\%` is a literal.
_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
_DEF_KW_RE = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\b\*?")
_DECL_OP_RE = re.compile(r"\\DeclareMathOperator\b(\*?)")
_SIMPLE_DEF_RE = re.compile(r"\\def\s*\\([A-Za-z]+|.)\s*\{")
# `{\name}` or bare `\name` (letters, or a single non-letter like `1`).
_BRACED_NAME_RE = re.compile(r"\{\s*\\([A-Za-z]+|.)\s*\}")
_BARE_NAME_RE = re.compile(r"\\([A-Za-z]+|.)")
_OPT_ARG_RE = re.compile(r"\s*\[([^\]]*)\]")


def _read_braced(s: str, i: int) -> tuple[str | None, int]:
    """If ``s[i]`` opens a brace group, return ``(inner, index_after_close)``;
    otherwise ``(None, i)``. Brace-aware and skips ``\\{``/``\\}`` escapes, so
    nested groups like ``\\mathcal{D_{\\mathrm{valid}}}`` parse correctly."""
    if i >= len(s) or s[i] != "{":
        return None, i
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":  # skip the escaped char so \{ / \} don't shift depth
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return None, i  # unbalanced — caller skips this definition


def _parse_newcommands(text: str, out: dict[str, MacroValue]) -> None:
    for m in _DEF_KW_RE.finditer(text):
        k = m.end()
        name_m = _BRACED_NAME_RE.match(text, k) or _BARE_NAME_RE.match(text, k)
        if not name_m:
            continue
        name = name_m.group(1)
        k = name_m.end()
        nargs = 0
        default: str | None = None
        if (opt := _OPT_ARG_RE.match(text, k)) is not None:
            try:
                nargs = int(opt.group(1))
            except ValueError:
                continue
            k = opt.end()
            if (opt2 := _OPT_ARG_RE.match(text, k)) is not None:
                default = opt2.group(1)
                k = opt2.end()
        # Skip any whitespace before the body brace.
        while k < len(text) and text[k] in " \t":
            k += 1
        body, _ = _read_braced(text, k)
        if body is None:
            continue
        if nargs == 0:
            out[name] = body
        elif default is None:
            out[name] = [body, nargs]
        else:
            out[name] = [body, nargs, default]


def _parse_declare_operators(text: str, out: dict[str, MacroValue]) -> None:
    for m in _DECL_OP_RE.finditer(text):
        star = m.group(1)
        k = m.end()
        name_m = _BRACED_NAME_RE.match(text, k)
        if not name_m:
            continue
        k = name_m.end()
        while k < len(text) and text[k] in " \t":
            k += 1
        op, _ = _read_braced(text, k)
        if op is None:
            continue
        out[name_m.group(1)] = rf"\operatorname{star}{{{op}}}"


def _parse_simple_defs(text: str, out: dict[str, MacroValue]) -> None:
    for m in _SIMPLE_DEF_RE.finditer(text):
        # The regex consumes up to and including the opening `{`; re-read the
        # body brace-aware starting at that `{`.
        body, _ = _read_braced(text, m.end() - 1)
        if body is None:
            continue
        out[m.group(1)] = body


def extract_macros(preamble: str) -> dict[str, MacroValue]:
    """Parse macro definitions from a LaTeX preamble into MathJax ``tex.macros``
    entries. Later definitions override earlier ones (LaTeX ``\\renewcommand``
    semantics). Unparseable definitions are skipped."""
    if not preamble:
        return {}
    text = _COMMENT_RE.sub("", preamble)
    out: dict[str, MacroValue] = {}
    # Order matters only for override semantics; the three forms rarely collide.
    _parse_newcommands(text, out)
    _parse_declare_operators(text, out)
    _parse_simple_defs(text, out)
    return out


def extract_macros_from_dir(source_dir: Path, preamble: str = "") -> dict[str, MacroValue]:
    """Macros from the paper's LOCAL ``.cls``/``.sty`` files merged under the
    main ``.tex`` preamble.

    ``extract_latex`` only reaches the main ``.tex`` preamble (+ inlined
    ``\\input`` of ``.tex`` files); a paper that ships its math macros in a
    custom document class or style package loaded via
    ``\\documentclass``/``\\usepackage`` (arXiv:2407.15595 defines ``\\gD``,
    ``\\sI``, ``\\dummy`` in ``fairmeta.cls``) loses ALL of them, so the
    Citation Canvas renders "Undefined control sequence" throughout. Parse the
    bundled class/style files too. Precedence: the main preamble wins on a key
    collision (it's the author's final say), then ``.sty``/``.cls`` in sorted
    order. Only top-level bundled files are scanned — system packages aren't in
    the tarball, so this stays scoped to the paper's own definitions.

    Class/style files are mostly LAYOUT machinery, not math. Two filters keep
    that junk out of the math config: (1) skip any definition whose body uses an
    ``@``-internal command (e.g. ``\\footnotesize`` → ``\\@setfontsize...``) —
    these break MathJax; (2) never let a bundled file override a CURATED macro
    (a class redefining ``\\footnotesize`` as a font-size switch must not clobber
    our safe no-op). Definitions from the main ``.tex`` preamble are trusted as
    the author's math macros and skip both filters.
    """
    out: dict[str, MacroValue] = {}
    for path in sorted([*source_dir.glob("*.cls"), *source_dir.glob("*.sty")]):
        try:
            parsed = extract_macros(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        for name, body in parsed.items():
            if name in CURATED_MACROS or _body_has_internal(body):
                continue  # layout machinery / would clobber a curated safe def
            out[name] = body
    out.update(extract_macros(preamble))  # main preamble overrides bundled files
    return out


def _body_has_internal(body: MacroValue) -> bool:
    """True if a macro body references an ``@``-internal command — the signature
    of LaTeX class/style layout machinery (``\\@setfontsize``, ``\\@ixpt``, …),
    which is never valid MathJax math and would surface as a render error."""
    text = body if isinstance(body, str) else (str(body[0]) if body else "")
    return "@" in text


def build_mathjax_config_script(extra: dict[str, MacroValue] | None = None) -> str:
    """Return a ``<script>`` that sets ``window.MathJax.tex.macros`` to the
    curated package macros merged with ``extra`` (author macros win on key
    collision). Must be injected BEFORE the MathJax loader ``<script>`` so the
    config is in place when MathJax initializes."""
    merged: dict[str, MacroValue] = {**CURATED_MACROS, **(extra or {})}
    payload = json.dumps(merged, ensure_ascii=False)
    # Defend against a macro body containing `</script>` closing the tag early.
    payload = payload.replace("</", "<\\/")
    return f"<script>window.MathJax={{tex:{{macros:{payload}}}}};</script>"
