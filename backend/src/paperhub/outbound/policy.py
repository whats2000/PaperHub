"""Outbound policy: the effective protection settings, resolved from env + profile."""
from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeVar

from paperhub.config import small_tier_model
from paperhub.settings_registry import (
    current_profile,
    effective_default,
    field_by_key,
    parse_string_list,
)

_N = TypeVar("_N", int, float)


@dataclass(frozen=True)
class OutboundPolicy:
    review_enabled: bool
    unsure: Literal["ask", "block"]
    terms: tuple[str, ...]
    allowlist: frozenset[str] | None  # None = allow all
    public_web_search: bool
    trusted_search_url: str | None
    private_upload_default: bool
    log_retention_days: int
    review_model: str
    review_timeout_s: float


def _num(kind: Callable[[str], _N], value: str, default: _N) -> _N:
    """Parse ``value`` with ``kind``; malformed input falls back to ``default``."""
    try:
        return kind(value)
    except ValueError:
        return default


def _timeout(value: str, default: float) -> float:
    """Finite and > 0, else ``default`` (nan/inf/0/negative never pass through)."""
    t = _num(float, value, default)
    return t if math.isfinite(t) and t > 0 else default


def load_policy(env: Mapping[str, str] | None = None) -> OutboundPolicy:
    """Resolve the policy: a non-empty explicit env value beats the profile
    default; an empty/blank value counts as unset (never fails open)."""
    e = os.environ if env is None else env
    profile = current_profile(e)

    def raw(key: str) -> str:
        v = e.get(key, "").strip()
        if v:
            return v
        f = field_by_key(key)
        assert f is not None, key
        return (effective_default(f, profile) or "").strip()

    def flag(key: str) -> bool:
        return raw(key).lower() in ("1", "true", "yes", "on")

    allow = parse_string_list(raw("PAPERHUB_OUTBOUND_ALLOWLIST"))
    allowlist = None if (not allow or "*" in allow) else frozenset(a.lower() for a in allow)
    unsure: Literal["ask", "block"] = (
        "block" if raw("PAPERHUB_OUTBOUND_UNSURE").lower() == "block" else "ask"
    )
    model = e.get("PAPERHUB_OUTBOUND_REVIEW_MODEL", "").strip()
    if not model:
        model = e.get("PAPERHUB_MODEL_SMALL") or small_tier_model()
    return OutboundPolicy(
        review_enabled=flag("PAPERHUB_OUTBOUND_REVIEW"),
        unsure=unsure,
        terms=parse_string_list(raw("PAPERHUB_SENSITIVE_TERMS")),
        allowlist=allowlist,
        public_web_search=flag("PAPERHUB_PUBLIC_WEB_SEARCH"),
        trusted_search_url=raw("PAPERHUB_TRUSTED_SEARCH_URL") or None,
        private_upload_default=flag("PAPERHUB_PRIVATE_UPLOAD_DEFAULT"),
        log_retention_days=max(7, _num(int, raw("PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS"), 365)),
        review_model=model,
        review_timeout_s=_timeout(raw("PAPERHUB_OUTBOUND_REVIEW_TIMEOUT"), 8.0),
    )


def locked_keys(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Registry keys the deployer pinned to the environment (``PAPERHUB_LOCKED_SETTINGS``).

    Comma-separated, trimmed, case-sensitive; unknown names are harmless (they
    never match a real key)."""
    e = os.environ if env is None else env
    return frozenset(k.strip() for k in e.get("PAPERHUB_LOCKED_SETTINGS", "").split(",") if k.strip())
