"""Data shapes of the outbound checkpoint (Plan H5a)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

PrivateKind = Literal["document", "chunk", "problem", "note"]
OutboundKind = Literal["web_search", "s2_search", "arxiv_search"]
#: ``discard``: the user chose not to send a held request (a paused turn).
Action = Literal["send", "ask", "hold", "block", "discard"]
Stage = Literal[
    "disabled", "allowlist", "exact_id", "terms", "cache",
    "review", "timeout", "error", "user", "span",
]


@dataclass
class PrivateItem:
    """One piece of private material in this turn's context."""

    kind: PrivateKind
    ref: str
    title: str
    text: str


#: What part of a note a ``note`` item holds.
NotePart = Literal["block", "comment", "title"]
_NOTE_REF = re.compile(r"^note:(\d+):(block|comment|title):([^#]*)(?:#[0-9a-f]+)?$")


def note_item_ref(note_id: int, part: NotePart, item_id: str = "", text: str = "") -> str:
    """The ``ref`` of a ``note`` item: ``note:<note id>:<part>:<id>``, plus
    ``#<hash>`` of ``text`` when given, so a block read and then rewritten in
    the same turn keeps both texts (items dedup by ref)."""
    ref = f"note:{note_id}:{part}:{item_id}"
    return f"{ref}#{hashlib.sha256(text.encode()).hexdigest()[:8]}" if text else ref


def parse_note_ref(ref: str) -> tuple[int, NotePart, str] | None:
    """``(note id, part, id)`` of a ``note_item_ref``, or None."""
    m = _NOTE_REF.match(ref)
    if m is None:
        return None
    return int(m.group(1)), m.group(2), m.group(3)  # type: ignore[return-value]


@dataclass
class OutboundRequest:
    caller: str
    kind: OutboundKind
    destination: str
    text: str
    unattended: bool = False
    run_id: int | None = None
    session_id: int | None = None


class ReviewVerdict(BaseModel):
    """The review model's answer (``outbound_review/v1``)."""

    action: Literal["allow", "rewrite", "ask", "block"]
    rewritten: str | None = None
    reason: str


@dataclass
class Decision:
    """What the checkpoint decided. ``ask`` / ``hold`` are logged as ``held``;
    ``stage == "user"`` means sent on the user's approval (Task 9)."""

    action: Action
    sent_text: str | None
    original_text: str
    stage: Stage
    reason: str
    log_id: int | None
    held_id: str | None
    #: The sensitive term that blocked the text (``stage == "terms"`` only).
    term: str | None = None
