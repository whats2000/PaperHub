"""Pre-rasterise complex LaTeX tables so pandoc can embed them as <img>.

Pandoc cannot parse the ``tabular*`` / ``tabularx`` environments at all (it
emits ``<div class="tabular*">`` and dumps the column spec + every &-separated
cell as raw text), and it mishandles ``\\multirow`` / ``\\makecell`` / dense
``\\multicolumn``+``\\cmidrule`` tables. arXiv:2602.20200's RoboTwin comparison
table (a 14-column ``tabular*`` with ``\\multirow`` headers) is the motivating
case. This module compiles each such table as a ``standalone`` document via
``pdflatex``, rasterises it to PNG, and rewrites the grid environment to
``\\includegraphics`` — leaving the surrounding ``table`` float + ``\\caption``
in place so pandoc still renders the caption as selectable text.

Mirrors ``tikz_figures.rasterize_tikz_figures``: ``pdflatex`` is already a hard
slide-pipeline dependency, failures are graceful (an un-compilable table is left
as-is), and the whole pass is a no-op when ``pdflatex`` is absent.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# A plain ``tabular`` is hostile only if its body uses constructs pandoc
# mishandles; ``tabular*`` / ``tabularx`` are hostile by environment.
_HOSTILE_BODY_RE = re.compile(r"\\multirow|\\makecell")
_MULTICOLUMN_RE = re.compile(r"\\multicolumn")
_CMIDRULE_RE = re.compile(r"\\cmidrule")


def _is_hostile(env_name: str, body: str) -> bool:
    """True if a table environment can't be reliably rendered by pandoc."""
    if env_name in ("tabular*", "tabularx"):
        return True
    if _HOSTILE_BODY_RE.search(body):
        return True
    return bool(_MULTICOLUMN_RE.search(body) and _CMIDRULE_RE.search(body))


# Match \begin{<name>} where <name> is a table family env. Order the
# alternation so the starred / x variants win over the bare "tabular".
_BEGIN_RE = re.compile(r"\\begin\{(tabular\*|tabularx|tabular)\}")


def _matching_end(tex: str, name: str, after: int) -> int:
    r"""Return the index just past the ``\end{name}`` matching the
    ``\begin{name}`` whose body starts at ``after``, counting same-name nesting.
    Returns -1 if unbalanced."""
    begin_tok = "\\begin{" + name + "}"
    end_tok = "\\end{" + name + "}"
    depth = 1
    i = after
    while i < len(tex):
        b = tex.find(begin_tok, i)
        e = tex.find(end_tok, i)
        if e == -1:
            return -1
        if b != -1 and b < e:
            depth += 1
            i = b + len(begin_tok)
        else:
            depth -= 1
            if depth == 0:
                return e + len(end_tok)
            i = e + len(end_tok)
    return -1


def _find_table_envs(tex: str) -> list[tuple[int, int, str]]:
    r"""Find every OUTERMOST tabular-family environment as ``(start, end,
    name)``. Env-depth-aware: a ``tabular`` nested inside a ``tabular*`` is part
    of the outer match, not returned separately (we jump past each outer env).
    Unbalanced begins are skipped."""
    envs: list[tuple[int, int, str]] = []
    i = 0
    while True:
        m = _BEGIN_RE.search(tex, i)
        if m is None:
            break
        name = m.group(1)
        end = _matching_end(tex, name, m.end())
        if end == -1:
            i = m.end()
            continue
        envs.append((m.start(), end, name))
        i = end  # skip the whole env so nested children aren't double-counted
    return envs
