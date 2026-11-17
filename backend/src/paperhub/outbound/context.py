"""Turn-level private material the outbound checkpoint reviews against.

Provenance is taken per turn (SRS H5a stage 4): tools that read private
documents, chunks of them, or an idea's problem text register the material
here; the checkpoint then judges every outbound query against all of it.
The list is mutated in place once it exists, so child tasks that copied the
context keep adding to the same turn's list.

Two budgets: the review model sees at most ``MAX_PRIVATE_CHARS`` of the
material (oldest chunk text trimmed first), while the verbatim-span guard
keeps the window digests of every item's FULL text, taken when it is added,
so trimming never takes a passage out of the guard.
"""
from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field

from paperhub.outbound.models import PrivateItem
from paperhub.outbound.spans import windows

__all__ = ["MAX_GUARD_WINDOWS", "MAX_PRIVATE_CHARS", "OutboundScope", "PrivateItem",
           "add_private", "begin_scope", "begin_turn", "outbound_scope",
           "current_private", "echoes_private", "guard_sources",
           "mark_incomplete", "material_incomplete", "private_material"]

logger = logging.getLogger(__name__)

#: Total private text the review sees; oldest chunk text is trimmed first.
MAX_PRIVATE_CHARS = 12_000
#: Distinct guard windows a turn may hold; past it the turn's material is
#: marked incomplete (the unsure policy), never silently left unguarded.
MAX_GUARD_WINDOWS = 200_000
_TOO_LARGE = "private material too large to guard"

private_material: ContextVar[list[PrivateItem]] = ContextVar("outbound_private_material")


# One mutable cell per turn (shared by reference with child tasks), holding
# the reason collection failed, or None when the material is complete.
_incomplete: ContextVar[list[str]] = ContextVar("outbound_material_incomplete")


@dataclass
class _Guard:
    """The span guard's digests for one turn's material list (``owner``):
    every window of each item's full text, by ``(kind, ref)``."""

    owner: list[PrivateItem]
    by_ref: dict[tuple[str, str], set[bytes]] = field(default_factory=dict)
    digests: set[bytes] = field(default_factory=set)


# One mutable cell per turn, shared by reference with child tasks.
_guard: ContextVar[_Guard] = ContextVar("outbound_span_guard")


def _digests(text: str) -> set[bytes]:
    """Digests of the verbatim-span windows of ``text`` (H5a windows)."""
    return {hashlib.sha256(w.encode("utf-8")).digest()[:16] for w in windows(text)}


def _turn_guard(items: list[PrivateItem]) -> _Guard:
    """The guard of ``items``; a context holding a new list gets a new guard."""
    try:
        guard = _guard.get()
    except LookupError:
        guard = None
    if guard is None or guard.owner is not items:
        guard = _Guard(items)
        _guard.set(guard)
    return guard


def _current_guard() -> _Guard | None:
    """This turn's guard. A non-empty material list without its guard (set
    directly, not through ``begin_turn`` / ``add_private``) never turns the
    guard off: it is rebuilt from the items, with a warning."""
    items = private_material.get(None)
    if items is None:
        return None
    guard = _guard.get(None)
    if guard is not None and guard.owner is items:
        return guard
    if not items:
        return None
    logger.warning("outbound: span guard rebuilt from %d private items "
                   "(material set without begin_turn/add_private)", len(items))
    guard = _turn_guard(items)
    for item in items:
        _guard_add(items, item)
    return guard


def echoes_private(text: str) -> bool:
    """``text`` repeats a verbatim-span window of this turn's private material."""
    guard = _current_guard()
    return guard is not None and not guard.digests.isdisjoint(_digests(text))


def guard_sources(text: str) -> list[tuple[str, str]]:
    """``(kind, ref)`` of every item ``text`` repeats a window of, in add order."""
    guard = _current_guard()
    if guard is None:
        return []
    mine = _digests(text)
    return [key for key, ds in guard.by_ref.items() if not mine.isdisjoint(ds)]


def _guard_add(items: list[PrivateItem], item: PrivateItem) -> None:
    guard = _turn_guard(items)
    mine = _digests(item.text)
    # Counted without copying the turn's set: an add costs its own windows.
    if len(guard.digests) + len(mine - guard.digests) > MAX_GUARD_WINDOWS:
        mark_incomplete(_TOO_LARGE)
        return
    guard.digests |= mine
    guard.by_ref.setdefault((item.kind, item.ref), set()).update(mine)


def begin_turn() -> None:
    """Start a turn with an empty private-material list (call once, in the
    turn's own task, before any tool runs). Also clears the incomplete flag."""
    items: list[PrivateItem] = []
    private_material.set(items)
    _incomplete.set([])
    _guard.set(_Guard(items))


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
    # Guarded on every add, a duplicate ref included: its text may differ.
    _guard_add(items, item)
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
