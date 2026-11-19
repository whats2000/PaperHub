"""XML pieces the core agent sees for the user's notes: shared by the note
tools and the session state (``context.py``). Note text is data, escaped."""
from __future__ import annotations

import html


def esc(value: object) -> str:
    """``value`` escaped for an XML attribute."""
    return html.escape(str(value), quote=True)


def title_xml(title: str) -> str:
    return f"<note_title>{html.escape(title, quote=False)}</note_title>"


MARK_BLOCKS = frozenset({"paperQuote", "paperFigure", "paperEquation", "paperRegion"})


def mark_xml(props: dict[str, object], labels: dict[str, str], body: str) -> str:
    """A reading mark as the agent sees it: what was marked, how it is drawn
    and what its colour MEANS to the user (the label, never the colour key).
    ``body`` is already escaped."""
    kind = str(props.get("kind") or "text")
    style = props.get("style")
    if kind == "highlight":  # a block written before kind + style
        kind, style = "text", "highlight"
    elif kind == "quote":
        kind, style = "text", "underline"
    color = props.get("color")
    label = labels.get(str(color)) if color else None
    attrs = f' kind="{esc(kind)}"'
    if style:
        attrs += f' style="{esc(style)}"'
    if label:
        attrs += f' label="{esc(label)}"'
    return f"<mark{attrs}>{body}</mark>"


def cite_runs_xml(content: object) -> str | None:
    """A block's runs, escaped, with each inline citation of a reading mark
    as ``<mark_cite mark="<markId>">alias</mark_cite>``; None when the block
    holds no such citation (its plain text then reads the same)."""
    if not isinstance(content, list) or not _has_cite(content):
        return None
    out: list[str] = []
    for run in content:
        if not isinstance(run, dict):
            continue
        props = run.get("props") or {}
        if run.get("type") == "markCite":
            alias = html.escape(str(props.get("text") or ""), quote=False)
            out.append(f'<mark_cite mark="{esc(props.get("markId") or "")}">'
                       f"{alias}</mark_cite>")
        elif run.get("type") == "inlineMath":
            out.append(html.escape(str(props.get("latex") or ""), quote=False))
        elif isinstance(run.get("text"), str):
            out.append(html.escape(run["text"], quote=False))
        if isinstance(run.get("content"), list):  # a link's runs
            out.append(cite_runs_xml(run["content"])
                       or html.escape(_plain(run["content"]), quote=False))
    return "".join(out)


def _has_cite(content: list[object]) -> bool:
    return any(isinstance(r, dict) and (
        r.get("type") == "markCite"
        or (isinstance(r.get("content"), list) and _has_cite(r["content"])))
        for r in content)


def _plain(content: list[object]) -> str:
    return "".join(str(r.get("text") or "") for r in content if isinstance(r, dict))
