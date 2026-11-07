"""Implicit-feedback keyword trigger (Plan H2, FR-19).

A correction keyword in the user's NEXT message flags the previous answer for a
background reviewer run. Only the user's own new message is matched — never an
assistant answer — and a match is a cheap pre-filter, not a verdict: "is it
wrong to use CEM here?" matches and the reviewer is expected to reject it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

DEFAULT_KEYWORDS: list[str] = [
    "不對", "錯了", "不是這個", "我是問", "答非所問",
    "wrong", "that's not", "not what i asked", "incorrect",
    "違う", "間違", "そうじゃない",
    # Polite and kana forms: "違います" contains neither "違う" nor "間違".
    # Not the bare stem "違い", which would match "AとBの違いは何ですか".
    "違います", "ちがう",
]

ENV_VAR = "PAPERHUB_FEEDBACK_KEYWORDS"


def keywords_from_env() -> list[str]:
    """Comma-separated ``PAPERHUB_FEEDBACK_KEYWORDS`` replaces the defaults;
    unset or blank falls back to a copy of ``DEFAULT_KEYWORDS``."""
    raw = os.environ.get(ENV_VAR, "")
    parsed = [k.strip() for k in raw.split(",") if k.strip()]
    return parsed or list(DEFAULT_KEYWORDS)


def matches_trigger(user_message: str, keywords: Iterable[str]) -> bool:
    """Case-insensitive substring match of any non-blank keyword."""
    text = user_message.strip().casefold()
    if not text:
        return False
    return any(k.strip() and k.strip().casefold() in text for k in keywords)
