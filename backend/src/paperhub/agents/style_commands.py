"""F4.5 deterministic style-command intercepts (no LLM).

Two commands short-circuit the slide pipeline:
  - "reset slide style" / "重置投影片樣式" → DELETE slide_style_overrides row
  - "remember this style for all future chats" → write/replace
    slide_style_global memory row from the current override

Either match emits a plain-text confirmation reply and skips the slide agent
entirely. Creative style mutations ("make it dark serif") do NOT match here —
they fall through to the slide agent's replace_preamble tool.
"""
from __future__ import annotations

import re
from typing import Literal

import aiosqlite

from paperhub.agents.style_resolver import clear_session_override, promote_to_global

StyleAction = Literal["reset_style", "promote_to_global"]

# Match-only patterns — pick the most explicit signal and avoid false positives
# on "make the slides dark" / "switch to a dark theme" (creative mutations).
_RESET_PATTERNS = (
    re.compile(r"\breset(?:\s+(?:the\s+)?slide)?\s+style\b", re.I),
    re.compile(r"重置(?:投影片)?樣式"),
    re.compile(r"清除(?:投影片)?樣式"),
)
_PROMOTE_PATTERNS = (
    re.compile(r"\bremember\s+(?:this\s+)?(?:slide\s+)?style\b.*\b(global(?:ly)?|all\s+(?:future\s+)?chats|all\s+sessions)\b", re.I),
    re.compile(r"\bmake\s+(?:this\s+)?(?:slide\s+)?style\s+(?:my\s+)?default\b", re.I),
    re.compile(r"(全域|永久)?(記住|儲存).*(樣式|風格)", re.I),
)


def classify_style_command(user_message: str) -> StyleAction | None:
    """Return the matched action or None for fall-through to the slide pipeline."""
    msg = user_message.strip()
    if any(p.search(msg) for p in _RESET_PATTERNS):
        return "reset_style"
    if any(p.search(msg) for p in _PROMOTE_PATTERNS):
        return "promote_to_global"
    return None


async def handle_style_command(
    *, action: StyleAction, session_id: int, conn: aiosqlite.Connection,
) -> str:
    """Apply the deterministic command and return a plain-text reply."""
    if action == "reset_style":
        had = await clear_session_override(session_id=session_id, conn=conn)
        if had:
            return (
                "Slide style reset — next generations and edits will use the default Beamer preamble "
                "(Berlin theme, dolphin colors, professionalfonts)."
            )
        return "No custom slide style was set for this session — already using the default."
    if action == "promote_to_global":
        ok = await promote_to_global(session_id=session_id, conn=conn)
        if ok:
            return (
                "Saved as your default slide style globally. New chats will inherit it; "
                "you can still tweak per-session via the slide agent or reset it any time."
            )
        return (
            "Nothing to save — no custom slide style is set for this session. "
            "Ask me to restyle the slides first, then run this again."
        )
    raise ValueError(f"unknown style action: {action!r}")
