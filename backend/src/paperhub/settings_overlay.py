# backend/src/paperhub/settings_overlay.py
"""Project DB-backed settings onto os.environ (Plan G / FR-14).

``load_settings()`` reads os.environ live per request, so mutating it
hot-applies. The first time a key is overridden we record its prior value so
clearing the override reverts to the .env / built-in default.
"""
from __future__ import annotations

import os

# key -> value held by os.environ BEFORE the first override (None = was unset).
_base: dict[str, str | None] = {}

# Sentinel distinct from any possible str | None env value, so a literal
# env value can never be mistaken for "never overridden".
_ABSENT: object = object()


def _record_base(key: str) -> None:
    if key not in _base:
        _base[key] = os.environ.get(key)


def set_override(key: str, value: str) -> None:
    _record_base(key)
    os.environ[key] = value


def clear_override(key: str) -> None:
    original = _base.pop(key, _ABSENT)
    if isinstance(original, str):
        os.environ[key] = original  # restore the pre-override value
    else:
        # never overridden (_ABSENT) or base was unset (None) -> remove it
        os.environ.pop(key, None)


def apply_overlay(rows: dict[str, str]) -> None:
    """Apply every DB row onto os.environ (records base first)."""
    for key, value in rows.items():
        set_override(key, value)


def reset_for_tests() -> None:
    # Revert every still-overridden key, then the base map is empty.
    for key in list(_base):
        clear_override(key)
