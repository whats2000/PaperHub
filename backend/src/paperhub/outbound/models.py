"""Data shapes of the outbound checkpoint (Plan H5a)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

PrivateKind = Literal["document", "chunk", "problem"]
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
