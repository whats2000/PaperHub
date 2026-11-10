"""Unwrap figure markup pandoc drops, so every figure shows its image.

pandoc renders nothing for ``\\subfigure`` / ``\\subfloat`` (subfigure /
subfig packages) or ``\\resizebox`` / ``\\scalebox`` bodies, and drops a whole
figure whose caption holds ``\\rule`` — the reader then shows an empty box,
and a ``[fig:P/ID]`` citation would scroll to nothing. Inside figure
environments only (the rest of the document is left byte-identical):

* ``\\subfigure[cap]{body}`` / ``\\subfloat[cap]{body}`` become
  ``body (a) cap`` — the panel letter the package prints, in order; without
  the optional argument, just ``body``. Never a sub-environment: pandoc
  numbers those as figures, which shifts every later "Fig. N" reference.
* ``\\resizebox{w}{h}{body}`` / ``\\scalebox{s}[t]{body}`` become ``body``.
* ``\\rule[raise]{w}{h}`` is removed.
"""
from __future__ import annotations

import re

from paperhub.pipelines.sentinels import optional_arg_end

_FIGURE_ENV_RE = re.compile(
    r"\\begin\{(figure\*?|wrapfigure)\}(.*?)\\end\{\1\}", re.DOTALL)
_COMMANDS_RE = re.compile(r"\\(subfigure|subfloat|resizebox|scalebox|rule)(?![A-Za-z])")


def _group_end(text: str, i: int) -> int:
    """Index just past the ``{...}`` group starting at ``text[i]`` (skipping
    leading whitespace), or -1 when there is none or it never closes."""
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if i >= len(text) or text[i] != "{":
        return -1
    depth = 0
    k = i
    while k < len(text):
        ch = text[k]
        if ch == "\\":
            k += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return -1


def _ends_in_comment(text: str) -> bool:
    """Whether the last line of ``text`` holds an unescaped ``%``."""
    line = text.rsplit("\n", 1)[-1]
    i = 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == "%":
            return True
        i += 1
    return False


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\n":
        i += 1
    return i


def _rewrite(body: str, panels: list[int] | None = None) -> str:
    # One panel counter per figure, shared by nested wrappers.
    panels = [0] if panels is None else panels
    out: list[str] = []
    pos = 0
    while (m := _COMMANDS_RE.search(body, pos)) is not None:
        name, k = m.group(1), m.end()
        replacement: str | None = None
        end = -1
        if name in ("subfigure", "subfloat"):
            opt_start = _skip_ws(body, k)
            opt_end = optional_arg_end(body, opt_start)
            has_label = opt_end > opt_start
            caption = body[opt_start + 1:opt_end - 1].strip() if has_label else ""
            g_end = _group_end(body, opt_end)
            if g_end != -1:
                g_start = body.index("{", opt_end)
                # Trimmed: the group's own line breaks would read as a blank
                # line between panels (a new row / paragraph).
                inner = _rewrite(body[g_start + 1:g_end - 1], panels).strip()
                if _ends_in_comment(inner):
                    inner += "\n"  # else the label lands inside the comment
                if has_label:
                    # The panel's "(a) caption", as the subfigure package
                    # prints it; never a sub-environment (pandoc would number
                    # it as a figure and shift every later figure number).
                    letter = chr(ord("a") + panels[0] % 26)
                    panels[0] += 1
                    inner = f"{inner} ({letter}) {caption} ".replace("  ", " ")
                replacement = inner
                end = g_end
        elif name in ("resizebox", "scalebox"):
            j = _group_end(body, k)                       # width / scale
            if j != -1 and name == "resizebox":
                j = _group_end(body, j)                   # height
            elif j != -1:
                j = optional_arg_end(body, _skip_ws(body, j))  # [vscale]
            g_end = _group_end(body, j) if j != -1 else -1
            if g_end != -1:
                g_start = body.index("{", j)
                replacement = _rewrite(body[g_start + 1:g_end - 1], panels)
                end = g_end
        else:  # \rule[raise]{w}{h}
            j = optional_arg_end(body, _skip_ws(body, k))
            j = _group_end(body, j)
            end = _group_end(body, j) if j != -1 else -1
            if end != -1:
                replacement = ""
        if replacement is None:
            out.append(body[pos:k])
            pos = k
            continue
        out.append(body[pos:m.start()])
        out.append(replacement)
        pos = end
    out.append(body[pos:])
    return "".join(out)


def unwrap_figure_wrappers(tex: str) -> str:
    """``tex`` with the wrappers above rewritten inside figure environments;
    a document without them is returned unchanged."""
    def fix(m: re.Match[str]) -> str:
        body = m.group(2)
        new = _rewrite(body)
        if new == body:
            return m.group(0)
        return m.group(0).replace(body, new, 1)

    return _FIGURE_ENV_RE.sub(fix, tex)
