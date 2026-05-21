"""Hidden chunk-start sentinels for deterministic Citation Canvas anchors.

A unique ASCII token is injected at each chunk's start offset in the
comment-stripped LaTeX *before* pandoc renders it; the token survives
latex->html rendering as plain text, and `postprocess_sentinels` rewrites each
into an empty `<span id="phchunk-N">`. The canvas then resolves a citation by
`getElementById`. Tokens that land inside math are skipped (a span there would
break MathJax); those chunks fall back to runtime text-search.
"""
from __future__ import annotations

import re


def sentinel_token(ordinal: int) -> str:
    """Return the unique ASCII sentinel token for *ordinal*.

    Format: ``PHCHUNKANCHOR{ordinal}END`` — no spaces, no special chars,
    survives pandoc reflowing.
    """
    return f"PHCHUNKANCHOR{ordinal}END"


# Matches the six LaTeX math environments we want to treat as opaque regions.
# Uses alternation ordered longest-first so the display/block forms take
# priority over the inline dollar form.  The named-group back-reference in the
# \begin{...}...\end{...} branch is required so the closing tag matches the
# opening environment name (including the optional star suffix).
_MATH_SPAN_RE = re.compile(
    r"\$\$.*?\$\$"  # display $$...$$
    r"|\$.*?\$"  # inline $...$
    r"|\\\(.*?\\\)"  # \( ... \)
    r"|\\\[.*?\\\]"  # \[ ... \]
    r"|\\begin\{(equation|align|eqnarray|math|displaymath|gather|multline)\*?\}"
    r".*?\\end\{\1\*?\}",
    re.DOTALL,
)


def find_math_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char spans of LaTeX math regions in *text*."""
    return [(m.start(), m.end()) for m in _MATH_SPAN_RE.finditer(text)]


# Environments where inserting plain text breaks pandoc / the LaTeX structure
# (tables, floats, code listings, graphics, matrix-like). Math environments are
# handled separately by `find_math_spans`. Anything NOT in this set (document,
# abstract, itemize, enumerate, quote, theorem, ...) is text-flow and safe to
# inject into. A sentinel inside one of these breaks rendering — observed in
# live re-render: pandoc exit 64 on `\end{table}` etc. — so we skip those.
_FRAGILE_ENVS = frozenset({
    "tabular", "tabularx", "tabular*", "longtable", "array", "table", "table*",
    "figure", "figure*", "wrapfigure", "subfigure", "tikzpicture", "picture",
    "verbatim", "verbatim*", "lstlisting", "minted", "algorithm", "algorithmic",
    "algorithm2e", "pmatrix", "bmatrix", "vmatrix", "matrix", "cases", "split",
    "tabbing", "supertabular",
})

_BEGIN_END_RE = re.compile(r"\\(begin|end)\s*\{([^}]*)\}")


def _safe_injection_mask(base: str) -> list[bool]:
    """For each index in *base*, whether injecting a plain-text sentinel there
    is LaTeX-safe: brace-depth 0, not inside math, and not inside a fragile
    environment (see `_FRAGILE_ENVS`). Also unsafe inside `\\begin{}`/`\\end{}`
    commands and command names themselves."""
    n = len(base)
    math = [False] * (n + 1)
    for s, e in find_math_spans(base):
        for k in range(s, min(e, n)):
            math[k] = True

    safe = [False] * (n + 1)
    brace = 0
    fragile = 0
    i = 0
    while i < n:
        ch = base[i]
        if ch == "\\":
            m = _BEGIN_END_RE.match(base, i)
            if m:
                kind, env = m.group(1), m.group(2).strip().rstrip("*")
                if env in _FRAGILE_ENVS:
                    fragile = fragile + 1 if kind == "begin" else max(0, fragile - 1)
                # The whole `\begin{}`/`\end{}` command span stays unsafe.
                i = m.end()
                continue
            # Other command or escaped char: skip the backslash + next char so a
            # literal `\{` / `\}` / `\\` isn't miscounted as a brace.
            i += 2
            continue
        if ch == "%":
            # Unescaped comment (\% was consumed above) → skip to end of line so
            # braces inside the comment aren't counted. The raw text we inject
            # into still has comments; this keeps the brace/env tracking honest.
            nl = base.find("\n", i)
            i = len(base) if nl < 0 else nl
            continue
        if ch == "{":
            brace += 1
        elif ch == "}":
            brace = max(0, brace - 1)
        safe[i] = brace == 0 and fragile == 0 and not math[i]
        i += 1
    safe[n] = brace == 0 and fragile == 0
    return safe


def inject_sentinels(
    base: str,
    starts: list[int],
) -> tuple[str, set[int]]:
    """Insert ``sentinel_token(i)`` at ``starts[i]`` in *base*.

    Inserts back-to-front so earlier offsets stay valid after each insertion.
    Skips any start that is not a LaTeX-safe injection point — inside math, a
    fragile environment (tables/floats/code), a brace group, or a command — so
    the sentinel never breaks pandoc rendering (those chunks fall back to
    runtime text-search). Returns ``(marked_text, injected_ordinals)``.
    """
    safe = _safe_injection_mask(base)

    injected: set[int] = set()
    # Process in descending position order (back-to-front) so each insertion
    # only shifts characters to its RIGHT — positions not yet processed (to the
    # LEFT) are unaffected, so `out[:pos]` always equals `base[:pos]`.
    order = sorted(range(len(starts)), key=lambda i: starts[i], reverse=True)
    out = base
    for i in order:
        pos = starts[i]
        if pos < 0 or pos > len(base) or not safe[pos]:
            continue
        out = out[:pos] + sentinel_token(i) + out[pos:]
        injected.add(i)
    return out, injected


_TOKEN_RE = re.compile(r"PHCHUNKANCHOR(\d+)END")


def postprocess_sentinels(html: str) -> tuple[str, dict[int, str]]:
    """Replace each surviving sentinel token in *html* with an empty
    ``<span id="phchunk-N">``.

    Returns ``(new_html, {ordinal: dom_id})`` for the tokens that survived
    rendering intact.  A token mangled or dropped by pandoc simply won't be
    found and is absent from the dict — those chunks fall back to runtime
    text-search.
    """
    found: dict[int, str] = {}

    def _sub(m: re.Match[str]) -> str:
        ordinal = int(m.group(1))
        dom_id = f"phchunk-{ordinal}"
        found[ordinal] = dom_id
        return f'<span id="{dom_id}"></span>'

    new_html = _TOKEN_RE.sub(_sub, html)
    return new_html, found
