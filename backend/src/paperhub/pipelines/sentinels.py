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


def inject_sentinels(
    base: str,
    starts: list[int],
) -> tuple[str, set[int]]:
    """Insert ``sentinel_token(i)`` at ``starts[i]`` in *base*.

    Inserts back-to-front so earlier offsets stay valid after each insertion.
    Skips any start that falls inside a detected math span (see
    `find_math_spans`).  Returns ``(marked_text, injected_ordinals)`` where
    ``injected_ordinals`` is the set of ordinal indices that were successfully
    inserted (a start outside ``[0, len(base)]`` or inside math is excluded).
    """
    math = find_math_spans(base)

    def _in_math(pos: int) -> bool:
        return any(s <= pos < e for s, e in math)

    injected: set[int] = set()
    # Process in descending position order (back-to-front) so each insertion
    # only shifts characters to its RIGHT.  Characters to the LEFT — which are
    # the positions we haven't processed yet — are unaffected.  We therefore
    # apply each insertion directly at `pos` in the *current* `out`: all
    # previously inserted tokens sit strictly to the right of `pos` in `out`,
    # so `out[:pos]` is identical to `base[:pos]` for every step.
    order = sorted(range(len(starts)), key=lambda i: starts[i], reverse=True)
    out = base
    for i in order:
        pos = starts[i]
        if pos < 0 or pos > len(base) or _in_math(pos):
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
