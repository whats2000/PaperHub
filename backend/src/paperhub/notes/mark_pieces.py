"""Erase order of a mark's pieces, shared by the mark API and the op
engine (which revives a mark when its block comes back)."""
from __future__ import annotations

from typing import Any


def live_pieces(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pieces still shown: neither erased nor moved to another mark."""
    return [p for p in pieces if not p.get("erased") and not p.get("removed")]


def _seq(p: dict[str, Any]) -> int:
    s = p.get("erased_seq")
    return s if isinstance(s, int) and not isinstance(s, bool) else 0


def erased(piece: dict[str, Any], pieces: list[dict[str, Any]]) -> dict[str, Any]:
    """``piece`` flagged erased, numbered after every piece erased before."""
    nxt = 1 + max((_seq(p) for p in pieces if p.get("erased")), default=0)
    return {**piece, "erased": True, "erased_seq": nxt}


def unerased(piece: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in piece.items() if k not in ("erased", "erased_seq")}


def revive_latest(pieces: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """``pieces`` with the most recently erased piece back, when none is
    live (a mark never comes back with nothing to draw). Pieces erased
    before the order was recorded count as sequence 0, so the last of
    them by index wins. ``None`` when no erased piece is left to revive."""
    if live_pieces(pieces):
        return pieces
    cands = [i for i, p in enumerate(pieces)
             if p.get("erased") and not p.get("removed")]
    if not cands:
        return None
    pick = max(cands, key=lambda i: (_seq(pieces[i]), i))
    return [unerased(p) if i == pick else p for i, p in enumerate(pieces)]
