"""How a block's inline runs read as text (the agent's reading): inline
math ``$…$``, a mention or note link its label (a note link without one its
target's name), a mark citation its alias or the quote's start. Shared by ``inline_merge`` (the per-run merge) and ``gfm_table``
(a table's cells)."""
from __future__ import annotations

from typing import Any

INLINE_NODES = frozenset({"markCite", "inlineMath", "mention", "noteLink"})


def cite_default_text(quote: str) -> str:
    """A mark citation's text when it has no alias (``notes.export``'s
    ``cite_default_text``; imported late, export imports the op engine)."""
    from paperhub.notes.export import cite_default_text as _default
    return _default(quote)


def link_text(props: dict[str, Any]) -> str:
    """A ``noteLink``'s text: its label, else the target's name at insert
    time (so nothing needs a lookup)."""
    return str(props.get("label") or props.get("name") or "")


def node_text(run: dict[str, Any], quotes: dict[str, str]) -> str:
    """How an inline node reads as text."""
    props = run.get("props") or {}
    kind = run.get("type")
    if kind == "markCite":
        alias = str(props.get("text") or "")
        return alias or cite_default_text(quotes.get(str(props.get("markId")), ""))
    if kind == "inlineMath":
        return f"${props.get('latex') or ''}$"
    if kind == "mention":
        return str(props.get("label") or "")
    if kind == "noteLink":  # a [[ link: its label, else the target's name
        return link_text(props)
    return ""


def read_runs(content: Any, quotes: dict[str, str] | None = None) -> str:
    """A block's runs as the agent reads them."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    q = quotes or {}
    out: list[str] = []
    for run in content:
        if not isinstance(run, dict):
            continue
        if run.get("type") in INLINE_NODES:
            out.append(node_text(run, q))
        elif isinstance(run.get("text"), str):
            out.append(run["text"])
        if isinstance(run.get("content"), list):  # a link's runs
            out.append(read_runs(run["content"], q))
    return "".join(out)
