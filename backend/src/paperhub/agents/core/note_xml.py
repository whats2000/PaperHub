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
