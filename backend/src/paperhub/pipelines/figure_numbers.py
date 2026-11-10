"""Give figure references in the rendered HTML the paper's own numbers.

pandoc numbers only the figures it renders as ``<figure>`` (not
``figure*`` divs, not figures it dropped), so "Fig. N" in the reader drifts
from the paper. LaTeX steps the figure counter at each top-level
``\\caption`` of a ``figure`` / ``figure*`` / ``wrapfigure`` (sub-figure
captions do not count; ``\\setcounter{figure}{N}`` resets it); a ``\\label``
takes the number of the caption it follows (or the env's first caption when
it comes before any). The render source gives the map; each
``<a data-reference-type="ref" data-reference="LABEL">`` to a figure label
gets that number. Other references (tables, sections, equations) are untouched.
"""
from __future__ import annotations

import re

_FIGURE_ENV = re.compile(
    r"\\begin\{(figure\*?|wrapfigure)\}(.*?)\\end\{\1\}", re.DOTALL)
_SETCOUNTER = re.compile(r"\\setcounter\{figure\}\{(\d+)\}")
_SUB_ENV = re.compile(r"\\begin\{subfigure\}.*?\\end\{subfigure\}", re.DOTALL)
_CAPTION_OR_LABEL = re.compile(
    r"\\caption(?![a-zA-Z])|\\label\{([^}]*)\}|\\captionof\{(\w+)\}")
_OTHER_FLOAT = re.compile(
    r"\\begin\{(table\*?|algorithm\*?|algorithm2e)\}.*?\\end\{\1\}", re.DOTALL)
_COMMENT_ENV = re.compile(r"\\begin\{comment\}.*?\\end\{comment\}", re.DOTALL)
_REF = re.compile(
    r'(<a\b[^>]*data-reference-type="ref"[^>]*data-reference="([^"]+)"[^>]*>)([^<]*)(</a>)')


def _strip_comments(tex: str) -> str:
    """Comments blanked (same length is not needed: only order matters)."""
    tex = _COMMENT_ENV.sub("", tex)
    return re.sub(r"(?<!\\)%[^\n]*", "", tex)


def _drop_subfigure_groups(body: str) -> str:
    """The body without sub-figure environments and ``\\subfigure`` /
    ``\\subfloat`` groups, whose captions and labels are the panels'."""
    body = _SUB_ENV.sub("", body)
    out, i = [], 0
    for m in re.finditer(r"\\(?:subfigure|subfloat)(?![a-zA-Z])", body):
        if m.start() < i:
            continue
        out.append(body[i:m.start()])
        k = m.end()
        while k < len(body) and body[k] in " \t\n":
            k += 1
        if k < len(body) and body[k] == "[":            # [sub-caption]
            depth = 0
            while k < len(body):
                depth += body[k] == "["
                depth -= body[k] == "]"
                k += 1
                if depth == 0:
                    break
        while k < len(body) and body[k] in " \t\n":
            k += 1
        if k < len(body) and body[k] == "{":            # {body}
            depth = 0
            while k < len(body):
                if body[k] == "\\":
                    k += 2
                    continue
                depth += body[k] == "{"
                depth -= body[k] == "}"
                k += 1
                if depth == 0:
                    break
        i = k
    out.append(body[i:])
    return "".join(out)


def figure_label_numbers(tex: str) -> dict[str, str]:
    """``{figure label: "N"}`` in the paper's numbering."""
    tex = _strip_comments(tex)
    events = [(m.start(), "env", m) for m in _FIGURE_ENV.finditer(tex)]
    events += [(m.start(), "set", m) for m in _SETCOUNTER.finditer(tex)]
    numbers: dict[str, str] = {}
    counter = 0
    for _, kind, m in sorted(events, key=lambda e: e[0]):
        if kind == "set":
            counter = int(m.group(1))
            continue
        # Tables / algorithms nested in a figure count on their own counters.
        own = _OTHER_FLOAT.sub("", m.group(2))
        body = _drop_subfigure_groups(own)
        first: int | None = None
        current: int | None = None
        pending: list[str] = []
        for t in _CAPTION_OR_LABEL.finditer(body):
            if t.group(2) is not None and t.group(2) != "figure":
                current = -1                    # \captionof{table}: not a figure
                continue
            if t.group(1) is None:              # \caption / \captionof{figure}
                counter += 1
                current = counter
                if first is None:
                    first = counter
                    for label in pending:
                        numbers[label] = str(counter)
                    pending = []
            elif current is None:
                pending.append(t.group(1))
            elif current > 0:
                numbers[t.group(1)] = str(current)
        if first is not None:
            numbers.update(_panel_labels(own, first))
    return numbers


def _panel_labels(body: str, number: int) -> dict[str, str]:
    """Sub-figure labels, as each package prints them (checked against
    pdflatex .aux files): ``\\subfigure`` (subfigure) ``N(a)``; ``\\subfloat``
    (subfig) and ``subfigure`` environments (subcaption) ``Na``."""
    out: dict[str, str] = {}
    for n, env in enumerate(_SUB_ENV.finditer(body)):
        for label in re.findall(r"\\label\{([^}]*)\}", env.group(0)):
            out[label] = f"{number}{chr(ord('a') + n % 26)}"
    without_envs = _SUB_ENV.sub("", body)
    parts = re.split(r"\\(subfigure|subfloat)(?![a-zA-Z])", without_envs)
    for n, (command, group) in enumerate(zip(parts[1::2], parts[2::2], strict=True)):
        letter = chr(ord("a") + n % 26)
        tag = f"{number}({letter})" if command == "subfigure" else f"{number}{letter}"
        # The group runs to the next panel; its own labels come first.
        for label in re.findall(r"\\label\{([^}]*)\}", _first_group(group)):
            out[label] = tag
    return out


def _first_group(text: str) -> str:
    """``[opt]{body}`` at the start of ``text``: the option and body text."""
    i = 0
    while i < len(text) and text[i] in " \t\n":
        i += 1
    start = i
    for opener, closer in (("[", "]"), ("{", "}")):
        while i < len(text) and text[i] in " \t\n":
            i += 1
        if i < len(text) and text[i] == opener:
            depth = 0
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                depth += text[i] == opener
                depth -= text[i] == closer
                i += 1
                if depth == 0:
                    break
    return text[start:i]


def renumber_figure_refs(html: str, numbers: dict[str, str]) -> str:
    """References to figure labels carry the paper's figure numbers."""
    if not numbers:
        return html

    def fix(m: re.Match[str]) -> str:
        n = numbers.get(m.group(2))
        return f"{m.group(1)}{n}{m.group(4)}" if n is not None else m.group(0)

    return _REF.sub(fix, html)


def renumber_from_source(html: str, flattened: str) -> str:
    """``renumber_figure_refs`` with numbers from the flattened source (before
    the render unwraps sub-figures, whose labels it needs)."""
    return renumber_figure_refs(html, figure_label_numbers(flattened))
