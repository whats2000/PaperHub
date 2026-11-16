"""Fractional order keys (base-62) for block siblings.

Keys compare as plain strings. ``key_between(a, b)`` returns a key ``k`` with
``a < k < b`` (a ``None`` bound is open). Generated keys never end in the zero
digit, so a key can always be squeezed between any two neighbours; the lone
exception is the seed ``"a0"``.
"""
from __future__ import annotations

_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_BASE = len(_DIGITS)
_FIRST = "a0"


def _mid(a: str, b: str | None) -> str:
    """A fraction strictly between ``a`` and ``b`` (``a`` zero-padded)."""
    if b is not None:
        n = 0
        while n < len(b) and (a[n] if n < len(a) else "0") == b[n]:
            n += 1
        if n > 0:
            return b[:n] + _mid(a[n:], b[n:] or None)
    da = _DIGITS.index(a[0]) if a else 0
    db = _DIGITS.index(b[0]) if b else _BASE
    if db - da > 1:
        return _DIGITS[(da + db + 1) // 2]
    if b and len(b) > 1:
        return b[:1]
    return _DIGITS[da] + _mid(a[1:], None)


def key_between(a: str | None, b: str | None) -> str:
    if a is None and b is None:
        return _FIRST
    if a is not None and b is not None and a >= b:
        raise ValueError(f"key_between needs a < b, got {a!r} >= {b!r}")
    if a is None:
        assert b is not None
        return _mid("", b)
    if b is not None:
        return _mid(a, b)
    if a[-1] == "z":
        return a + _mid("", None)
    return a[:-1] + _DIGITS[_DIGITS.index(a[-1]) + 1]
