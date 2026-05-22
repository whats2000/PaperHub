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


def _mask_noncontent_dollars(text: str) -> str:
    """Return a copy of *text* (same length, offsets preserved) with the two
    classes of ``$`` that LaTeX does NOT treat as math delimiters blanked to a
    space, so `_MATH_SPAN_RE`'s sequential ``$...$`` pairing only ever sees real
    inline-math dollars:

    1. **Escaped** ``\\$`` — a literal dollar sign in body text.
    2. Dollars **inside ``%`` comments** — not math at all.

    Without this, a single stray ``$`` (one paper observed had 67 in comments +
    one escaped) shifts every subsequent pairing, so a *closing* ``$`` is read
    as an *opener* and the regex swallows tens of thousands of characters of
    prose as one giant "math" span — which marks that whole region unsafe and
    silently kills chunk anchoring for the rest of the document.

    Math-env and bracket delimiters (``\\(``, ``\\[``, ``\\begin{equation}`` …)
    are left intact: the backslash-escape skip steps past ``\\X`` without
    altering it; only an escaped dollar's ``$`` is blanked.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == "\\":
            # Escaped pair: blank a literal `\$`'s dollar; leave `\(`, `\[`,
            # `\begin`, etc. untouched. Skip both chars so an escaped `\%`
            # doesn't start a comment.
            if i + 1 < n and text[i + 1] == "$":
                out[i + 1] = " "
            i += 2
            continue
        if ch == "%":
            # Unescaped comment → blank to end of line (the `$`s inside are not
            # math). `\%` was consumed by the branch above, so this is a real
            # comment start.
            nl = text.find("\n", i)
            end = n if nl < 0 else nl
            for k in range(i, end):
                out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def find_math_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char spans of LaTeX math regions in *text*.

    Dollars inside comments and escaped ``\\$`` are neutralised first (see
    `_mask_noncontent_dollars`) so ``$...$`` pairing doesn't drift; spans are
    reported against the ORIGINAL offsets (the mask preserves length)."""
    masked = _mask_noncontent_dollars(text)
    return [(m.start(), m.end()) for m in _MATH_SPAN_RE.finditer(masked)]


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
            # Control word (`\` + letters) or control symbol (`\` + a single
            # non-letter, e.g. `\$`, `\%`, `\\`, `\{`). The whole token stays
            # unsafe — injecting inside a command name corrupts it (default
            # `safe` is False; we just advance past it without setting True).
            j = i + 1
            if j < n and base[j].isalpha():
                while j < n and base[j].isalpha():
                    j += 1
            else:
                j = i + 2
            i = j
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
            # The brace char itself is not an injection point.
            i += 1
            continue
        if ch == "}":
            brace = max(0, brace - 1)
            # Likewise the closing brace — only positions AFTER it are safe, so
            # an anchor never lands between content and its closing brace.
            i += 1
            continue
        safe[i] = brace == 0 and fragile == 0 and not math[i]
        i += 1
    safe[n] = brace == 0 and fragile == 0
    return safe


def inject_sentinels(
    base: str,
    starts: list[int],
) -> tuple[str, set[int]]:
    """Insert ``sentinel_token(i)`` as an anchor for the chunk beginning at
    ``starts[i]`` in *base*.

    The token is placed at the chunk's start when that position is a LaTeX-safe
    injection point. When the start is unsafe (inside math, a fragile
    environment, a brace group, or a command — see `_safe_injection_mask`), the
    anchor FALLS BACK to a nearby safe point instead of being dropped, so the
    chunk is still anchored close to its content:

    - **forward** to the first safe point within the chunk's own span (bounded
      by the next chunk's start) — lands on the chunk's own prose, e.g. just
      past a leading ``\\textbf{...}`` / ``\\paragraph{...}`` / ``\\label{...}``;
    - else **backward** to the nearest safe point before the start (bounded by
      the previous chunk's start) — lands just before e.g. the ``\\begin{table}``
      a pure table-content chunk lives in, so a citation scrolls to the table
      rather than dead-ending at the section heading.

    The token is NEVER placed inside an unsafe span (that would break pandoc /
    MathJax) — only at a safe fallback near it. Inserts back-to-front so earlier
    offsets stay valid. Returns ``(marked_text, injected_ordinals)``.
    """
    n = len(base)
    safe = _safe_injection_mask(base)

    # Document-order traversal so each chunk's fallback search can be bounded by
    # its neighbours (forward stays inside this chunk; backward stays after the
    # previous chunk).
    order = sorted(range(len(starts)), key=lambda i: starts[i])

    resolved: dict[int, int] = {}  # ordinal -> chosen injection position
    for k, i in enumerate(order):
        pos = starts[i]
        if pos < 0 or pos > n:
            continue
        next_start = starts[order[k + 1]] if k + 1 < len(order) else n
        prev_start = starts[order[k - 1]] if k > 0 else 0
        if safe[pos]:
            resolved[i] = pos
            continue
        # Forward within this chunk's span [pos, next_start).
        fwd = next(
            (q for q in range(pos, min(next_start, n)) if safe[q]),
            None,
        )
        if fwd is not None:
            resolved[i] = fwd
            continue
        # Backward to the nearest safe point, not crossing into the previous
        # chunk (exclusive of prev_start so we don't collide with its anchor).
        bwd = next(
            (q for q in range(min(pos, n), prev_start, -1) if safe[q]),
            None,
        )
        if bwd is not None:
            resolved[i] = bwd

    # Insert back-to-front. Sorting by (position DESC, ordinal DESC) keeps each
    # insertion shifting only characters to its right, and when several chunks
    # resolve to the SAME position the lowest ordinal ends up leftmost (document
    # order preserved within the tie group).
    out = base
    for i in sorted(resolved, key=lambda j: (resolved[j], j), reverse=True):
        pos = resolved[i]
        out = out[:pos] + sentinel_token(i) + out[pos:]

    return out, set(resolved)


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
