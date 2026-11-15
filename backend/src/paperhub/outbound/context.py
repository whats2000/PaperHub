"""Turn-level private material the outbound checkpoint reviews against.

Provenance is taken per turn (SRS H5a stage 4): tools that read private
documents, chunks of them, or an idea's problem text register the material
here; the checkpoint then judges every outbound query against all of it.
The list is mutated in place once it exists, so child tasks that copied the
context keep adding to the same turn's list.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from paperhub.outbound.models import PrivateItem
from paperhub.outbound.spans import windows

__all__ = ["MAX_PRIVATE_CHARS", "OutboundScope", "PrivateItem", "add_private",
           "begin_scope", "begin_turn", "outbound_scope",
           "current_private", "mark_incomplete", "material_incomplete",
           "private_material", "private_windows"]

logger = logging.getLogger(__name__)

#: Total private text the review sees; oldest chunk text is trimmed first.
MAX_PRIVATE_CHARS = 12_000

private_material: ContextVar[list[PrivateItem]] = ContextVar("outbound_private_material")


# One mutable cell per turn (shared by reference with child tasks), holding
# the reason collection failed, or None when the material is complete.
_incomplete: ContextVar[list[str]] = ContextVar("outbound_material_incomplete")


# [signature, windows] of this turn's private text, built once and rebuilt only
# when the material changes (a mutable cell, shared with child tasks).
_windows_cache: ContextVar[list[Any]] = ContextVar("outbound_private_windows")


def private_windows() -> frozenset[str]:
    """The verbatim-span windows of this turn's private material (memoised)."""
    items = private_material.get([])
    sig = tuple((i.kind, i.ref, hash(i.text)) for i in items)
    try:
        cell = _windows_cache.get()
    except LookupError:
        cell = []
        _windows_cache.set(cell)
    if cell and cell[0] == sig:
        return cell[1]  # type: ignore[no-any-return]
    ws = frozenset(w for i in items for w in windows(i.text))
    cell[:] = [sig, ws]
    return ws


def begin_turn() -> None:
    """Start a turn with an empty private-material list (call once, in the
    turn's own task, before any tool runs). Also clears the incomplete flag."""
    private_material.set([])
    _incomplete.set([])
    _windows_cache.set([])


def mark_incomplete(reason: str) -> None:
    """Record that this turn's private material could not be fully collected
    (the checkpoint then cannot trust an empty or partial list)."""
    try:
        cell = _incomplete.get()
    except LookupError:
        cell = []
        _incomplete.set(cell)
    cell.append(reason)


def material_incomplete() -> str | None:
    """The first collection-failure reason of this turn, or None."""
    return next(iter(_incomplete.get([])), None)


def current_private() -> list[PrivateItem]:
    """A copy of this turn's private material (empty when none)."""
    return list(private_material.get([]))


def _trim(items: list[PrivateItem]) -> None:
    excess = sum(len(i.text) for i in items) - MAX_PRIVATE_CHARS
    if excess <= 0:
        return
    # Oldest chunk text goes first, then the oldest of any kind.
    order = [i for i in items if i.kind == "chunk"] + [i for i in items if i.kind != "chunk"]
    for item in order:
        if excess <= 0:
            break
        cut = min(excess, len(item.text))
        item.text = item.text[: len(item.text) - cut]
        excess -= cut


def add_private(item: PrivateItem) -> None:
    """Register ``item`` for this turn (dedup by ``(kind, ref)``, capped)."""
    try:
        items = private_material.get()
    except LookupError:
        # No begin_turn() in this context: the list would live only in this
        # (possibly child) context and the turn's checkpoint might miss it.
        logger.warning("outbound: add_private without begin_turn(); %s:%s",
                       item.kind, item.ref)
        items = []
        private_material.set(items)
    if any(i.kind == item.kind and i.ref == item.ref for i in items):
        return
    items.append(PrivateItem(item.kind, item.ref, item.title, item.text))
    _trim(items)


@dataclass(frozen=True)
class OutboundScope:
    run_id: int
    session_id: int


#: The agent turn this task belongs to, for audit rows written by code that
#: has no connection or run id (the LLM adapter). Unset outside a turn.
outbound_scope: ContextVar[OutboundScope | None] = ContextVar("outbound_scope", default=None)


def begin_scope(run_id: int, session_id: int) -> None:
    """Bind this turn's run/session ids (call next to ``begin_turn``)."""
    outbound_scope.set(OutboundScope(run_id, session_id))
