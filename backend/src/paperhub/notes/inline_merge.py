"""Reading a block's inline runs as text, and writing plain text back over
them without losing what the text did not change.

The agent reads a block as plain text (an inline formula as ``$…$``, a
mark citation as its alias or the quote's start, a mention as its label)
and may write a plain string back. ``preserve_runs`` diffs the old reading
against that string and rebuilds the runs: unchanged spans keep their runs
and styles (comment anchors are a style, so they stay), an inline node
survives when its whole text span is unchanged, and inserted text takes
the style of the run it lands inside (plain at a run boundary).
"""
from __future__ import annotations

import difflib
import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import aiosqlite

# Blocks that own a ``note_marks`` row (the mark follows the block).
MARK_BLOCK_TYPES = frozenset(
    {"paperQuote", "paperFigure", "paperEquation", "paperRegion"})
# The props of a mark block an agent edit may change: how it is drawn.
# Everything else (markId, quote, kind, pieces, source, anchors) is the
# mark's identity and is kept from the stored block.
MARK_EDITABLE_PROPS = frozenset({"color", "style"})

_INLINE_NODES = frozenset({"markCite", "inlineMath", "mention"})
_CITE_TAG = re.compile(r'<mark_cite mark="[^"]*">(.*?)</mark_cite>', re.S)


def merge_props(block_type: str, old: dict[str, Any],
                new: dict[str, Any]) -> dict[str, Any]:
    """An update's props over the stored ones: keys it omits keep their
    value; on a mark block only the drawing props can change."""
    if block_type not in MARK_BLOCK_TYPES:
        return {**old, **new}
    return {**old, **{k: v for k, v in new.items() if k in MARK_EDITABLE_PROPS}}


def cite_default_text(quote: str) -> str:
    """A mark citation's text when it has no alias (``notes.export``'s
    ``cite_default_text``; imported late, export imports the op engine)."""
    from paperhub.notes.export import cite_default_text as _default
    return _default(quote)


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
        if run.get("type") in _INLINE_NODES:
            out.append(node_text(run, q))
        elif isinstance(run.get("text"), str):
            out.append(run["text"])
        if isinstance(run.get("content"), list):  # a link's runs
            out.append(read_runs(run["content"], q))
    return "".join(out)


def read_text(block_type: str, props: dict[str, Any], content: Any,
              quotes: dict[str, str] | None = None) -> str:
    """A block's text as the agent reads it (a math block as ``$$…$$``)."""
    if block_type == "math":
        return f"$${props.get('latex') or ''}$$"
    if block_type == "section":
        return str(props.get("title") or "")
    if block_type == "paperQuote":
        return str(props.get("quote") or "")
    if block_type == "codeBlock":
        return read_runs(content, quotes) or str(props.get("code") or "")
    return read_runs(content, quotes)


def cite_ids(content: Any) -> set[str]:
    """The mark ids the block's inline citations name."""
    ids: set[str] = set()
    if not isinstance(content, list):
        return ids
    for run in content:
        if not isinstance(run, dict):
            continue
        if run.get("type") == "markCite":
            mid = (run.get("props") or {}).get("markId")
            if isinstance(mid, str) and mid:
                ids.add(mid)
        ids |= cite_ids(run.get("content"))
    return ids


async def cite_quotes(conn: aiosqlite.Connection,
                      mark_ids: set[str]) -> dict[str, str]:
    """The current quote of each live mark in ``mark_ids``."""
    if not mark_ids:
        return {}
    from paperhub.notes.mark_pieces import live_pieces
    from paperhub.notes.marks import joined_quote
    marks = ",".join("?" * len(mark_ids))
    async with conn.execute(
        f"SELECT id, pieces_json FROM note_marks WHERE id IN ({marks}) "  # noqa: S608
        "AND deleted_at IS NULL", tuple(sorted(mark_ids)),
    ) as cur:
        rows = await cur.fetchall()
    out: dict[str, str] = {}
    for mid, raw in rows:
        try:
            pieces = json.loads(raw or "[]")
        except ValueError:
            pieces = []
        out[str(mid)] = joined_quote(live_pieces(pieces if isinstance(pieces, list)
                                                 else []))
    return out


def is_rich(content: Any) -> bool:
    """Whether runs hold more than plain unstyled text."""
    if not isinstance(content, list):
        return False
    for run in content:
        if not isinstance(run, dict):
            continue
        if run.get("type") not in (None, "text") or run.get("styles"):
            return True
        if isinstance(run.get("content"), list):
            return True
    return False


def unwrap_cite_tags(text: str) -> str:
    """The agent's string with any ``<mark_cite>`` tags it echoed back from a
    read replaced by their text (the read escaped the text around them)."""
    if "<mark_cite" not in text:
        return text
    return html.unescape(_CITE_TAG.sub(lambda m: m.group(1), text))


@dataclass
class _Leaf:
    start: int
    text: str
    run: dict[str, Any]
    node: bool
    link: int | None  # index into the links list


def _leaves(content: list[Any], quotes: dict[str, str]
            ) -> tuple[list[_Leaf], list[dict[str, Any]]]:
    leaves: list[_Leaf] = []
    links: list[dict[str, Any]] = []
    pos = 0

    def walk(runs: list[Any], link: int | None) -> None:
        nonlocal pos
        for run in runs:
            if not isinstance(run, dict):
                continue
            if isinstance(run.get("content"), list):
                links.append({k: v for k, v in run.items() if k != "content"})
                walk(run["content"], len(links) - 1)
                continue
            node = run.get("type") in _INLINE_NODES or not isinstance(
                run.get("text"), str)
            text = node_text(run, quotes) if node else str(run["text"])
            leaves.append(_Leaf(pos, text, run, node, link))
            pos += len(text)

    walk(content, None)
    return leaves, links


def _describe(leaf: _Leaf) -> dict[str, Any]:
    d: dict[str, Any] = {"type": leaf.run.get("type"), "text": leaf.text}
    mid = (leaf.run.get("props") or {}).get("markId")
    if mid:
        d["markId"] = mid
    return d


def preserve_runs(old: list[Any], new_text: str, quotes: dict[str, str]
                  ) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    """``(runs, report)``: ``new_text`` laid over the old runs. ``report``
    names the inline nodes ``kept`` and ``dropped``."""
    new_text = unwrap_cite_tags(new_text)
    leaves, links = _leaves(old, quotes)
    old_text = "".join(lf.text for lf in leaves)
    owner: list[int] = []
    for i, lf in enumerate(leaves):
        owner.extend([i] * len(lf.text))
    ops = _coarse(difflib.SequenceMatcher(None, old_text, new_text,
                                          autojunk=False).get_opcodes())
    same = [False] * len(old_text)
    for tag, i1, i2, _j1, _j2 in ops:
        if tag == "equal":
            for i in range(i1, i2):
                same[i] = True
    kept_node = {i for i, lf in enumerate(leaves) if lf.node and all(
        same[lf.start:lf.start + len(lf.text)])}

    # Pieces: ("t" kept / "i" inserted, text, styled leaf | None, link) or
    # ("n", leaf index).
    pieces: list[tuple[Any, ...]] = []
    emitted: set[int] = set()

    def flush_empty(upto: int) -> None:
        """Zero-width nodes (nothing to diff) stay where they stood."""
        for i, lf in enumerate(leaves):
            if lf.node and not lf.text and lf.start <= upto and i not in emitted:
                emitted.add(i)
                pieces.append(("n", i))

    def host(i1: int, i2: int) -> _Leaf | None:
        """The text run new text lands inside, None at a boundary."""
        if i2 > i1:  # a replacement: inside one run only
            a, b = owner[i1], owner[i2 - 1]
            lf = leaves[a]
            return lf if a == b and not lf.node else None
        if 0 < i1 < len(old_text) and owner[i1 - 1] == owner[i1]:
            lf = leaves[owner[i1]]
            return None if lf.node else lf
        return None

    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for i in range(i1, i2):
                flush_empty(i)
                li = owner[i]
                lf = leaves[li]
                if lf.node and li in kept_node:
                    if li not in emitted:
                        emitted.add(li)
                        pieces.append(("n", li))
                elif lf.node:
                    pieces.append(("t", old_text[i], None, lf.link))
                else:
                    pieces.append(("t", old_text[i], lf, lf.link))
        elif tag in ("insert", "replace"):
            flush_empty(i1)
            into = host(i1, i2)
            pieces.append(("i", new_text[j1:j2], into,
                           into.link if into else None))
    flush_empty(len(old_text) + 1)

    runs: list[dict[str, Any]] = []
    link_of: list[int | None] = []
    for p in pieces:
        if p[0] == "n":
            lf = leaves[p[1]]
            runs.append(dict(lf.run))
            link_of.append(lf.link)
            continue
        _, text, lf, link = p
        styles = dict(lf.run.get("styles") or {}) if lf else {}
        if p[0] == "i":
            # A comment anchors the words it was made on, never new ones.
            styles.pop("comment", None)
        prev = runs[-1] if runs else None
        if (prev is not None and prev.get("type") == "text"
                and prev.get("_src") is (lf.run if lf else None)
                and prev.get("styles") == styles and link_of[-1] == link):
            prev["text"] += text
            continue
        run = {**(lf.run if lf else {"type": "text"}), "text": text,
               "styles": styles, "_src": lf.run if lf else None}
        runs.append(run)
        link_of.append(link)
    for r in runs:
        r.pop("_src", None)
    runs, link_of = _merge_same_style(runs, link_of)
    out: list[dict[str, Any]] = []
    for r, link in zip(runs, link_of, strict=True):
        if link is None:
            out.append(r)
        elif out and out[-1].get("_link") == link:
            out[-1]["content"].append(r)
        else:
            out.append({**links[link], "content": [r], "_link": link})
    for r in out:
        r.pop("_link", None)
    report = {"kept": [_describe(leaves[i]) for i in sorted(kept_node)],
              "dropped": [_describe(lf) for i, lf in enumerate(leaves)
                          if lf.node and lf.text and i not in kept_node]}
    return out, report


def _coarse(ops: Sequence[tuple[str, int, int, int, int]]
            ) -> list[tuple[str, int, int, int, int]]:
    """Opcodes with a stray match of one or two characters between two
    changes folded into one replacement: a rewritten phrase that happens to
    share a letter is a rewrite, not a kept letter."""
    out: list[tuple[str, int, int, int, int]] = []
    for k, op in enumerate(ops):
        tag, i1, i2, j1, j2 = op
        stray = (tag == "equal" and i2 - i1 < 3 and 0 < k < len(ops) - 1)
        if stray or (tag != "equal" and out and out[-1][0] != "equal"):
            if out and out[-1][0] != "equal":
                _, a1, _a2, b1, _b2 = out[-1]
                out[-1] = ("replace", a1, i2, b1, j2)
            else:
                out.append(("replace", i1, i2, j1, j2))
            continue
        out.append(op)
    return out


def _merge_same_style(runs: list[dict[str, Any]], link_of: list[int | None]
                      ) -> tuple[list[dict[str, Any]], list[int | None]]:
    """Adjacent text runs with the same styles and link as one run."""
    out: list[dict[str, Any]] = []
    links: list[int | None] = []
    for r, link in zip(runs, link_of, strict=True):
        if (out and r.get("type") == "text" and out[-1].get("type") == "text"
                and links[-1] == link
                and {k: v for k, v in r.items() if k != "text"}
                == {k: v for k, v in out[-1].items() if k != "text"}):
            out[-1] = {**out[-1], "text": out[-1]["text"] + r["text"]}
            continue
        out.append(r)
        links.append(link)
    return [r for r in out if r.get("type") != "text" or r["text"]], [
        lk for r, lk in zip(out, links, strict=True)
        if r.get("type") != "text" or r["text"]]
