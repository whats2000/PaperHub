# backend/src/paperhub/settings_readiness.py
"""First-run readiness + live model discovery (frontend onboarding gate).

Two concerns, kept separate by reliability:

* **Readiness (hard gate)** — can the configured small + flagship models actually
  run right now? We *pre-flight the real call*: a 1-token ``acompletion`` against
  each gate model. This is the only check that catches every failure the user
  would otherwise hit on send — a missing key, an **empty/placeholder** key
  (``validate_environment`` reports an empty env var as "present"), an invalid or
  expired key, and a non-existent model id (e.g. ``gemini-3.1-pro-preview``).
  ``validate_environment`` is used only as a *fast short-circuit* when the key is
  plainly absent, so we skip the network call in that case. Results are cached
  (and invalidated on a settings PATCH) so boot isn't slow.

* **Model options (soft assist)** — autocomplete suggestions for the model-name
  fields. Best-effort live fetch via ``get_valid_models`` for providers that
  support discovery, falling back to LiteLLM's bundled static map. NEVER blocks:
  not every provider supports a list, so the model name stays free text.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import litellm

from paperhub.settings_registry import (
    LIVE_DISCOVERY_PROVIDERS,
    field_by_key,
    primary_key_for_model,
    provider_for_credential_key,
)

# The two model fields that must be runnable before the app is usable.
_GATE_MODEL_KEYS: tuple[tuple[str, str], ...] = (
    ("small", "PAPERHUB_MODEL_SMALL"),
    ("flagship", "PAPERHUB_MODEL_FLAGSHIP"),
)

_OPTIONS_TIMEOUT_S = 8.0
_OPTIONS_TTL_S = 600.0
# provider -> (fetched_at_monotonic, models). Live discovery is slow, so cache it.
_options_cache: dict[str, tuple[float, list[str]]] = {}

_PING_TIMEOUT_S = 12.0
_PING_RETRIES = 2  # transient-only; litellm won't retry auth / bad-model errors
_READINESS_TTL_S = 60.0
# model id -> (checked_at_monotonic, check dict). The ping is a real API call, so
# cache it; clear_readiness_cache() (called on a settings PATCH) forces a recheck.
_readiness_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _effective_model(env_key: str) -> str:
    field = field_by_key(env_key)
    default = field.default if field is not None else None
    return (os.environ.get(env_key) or "").strip() or default or ""


def _missing_keys(model: str) -> list[str]:
    """Provider keys this model needs but doesn't have. Beyond LiteLLM's own
    check, an **empty-valued** primary key is treated as missing — LiteLLM counts
    an empty env var as "present", which is the bug behind a removed `.env` key
    silently passing the gate."""
    try:
        env = litellm.validate_environment(model=model)
        if not env.get("keys_in_environment"):
            return list(env.get("missing_keys") or [])
    except Exception:  # noqa: BLE001 — never break the gate
        return []
    # "present" per LiteLLM — but flag an empty primary key as actually missing.
    key_name = primary_key_for_model(model)
    if key_name and not (os.environ.get(key_name) or "").strip():
        return [key_name]
    return []


async def _ping_model(model: str) -> dict[str, Any]:
    """Pre-flight a model with a 1-token completion. ``key_ok`` iff it succeeds.

    Fast short-circuit: if validate_environment says the key is plainly absent,
    report it without a network call. Otherwise we actually try the call so an
    empty/invalid key or a non-existent model id is caught (it would error on
    send otherwise)."""
    if not model:
        return {"model": "", "key_ok": False, "missing_keys": [], "error": None, "detail": None}

    missing = _missing_keys(model)
    if missing:  # key plainly absent — no point spending a network round-trip
        return {"model": model, "key_ok": False, "missing_keys": missing, "error": None, "detail": None}

    try:
        await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            timeout=_PING_TIMEOUT_S,
            # Retry transient provider blips (connection drops, timeouts, 429/5xx)
            # so a momentary network hiccup doesn't falsely lock the composer.
            # litellm fails fast on real errors (auth, bad model) — no retry there.
            num_retries=_PING_RETRIES,
        )
        return {"model": model, "key_ok": True, "missing_keys": [], "error": None, "detail": None}
    except Exception as exc:  # noqa: BLE001 — any failure means "not runnable"
        # Re-derive missing keys: an empty key surfaces here, not above. The
        # detail is the provider's own reason (redacted) so the UI can tell the
        # user whether it's the KEY or the MODEL — an opaque class name can't.
        return {
            "model": model,
            "key_ok": False,
            "missing_keys": _missing_keys(model),
            "error": type(exc).__name__,
            "detail": _redact_detail(str(exc)),
        }


def _redact_detail(message: str) -> str:
    """First line of a provider error, with anything key-shaped scrubbed."""
    head = message.strip().splitlines()[0] if message.strip() else ""
    # Scrub obvious secret-shaped tokens (sk-..., AIza..., long base64-ish runs).
    head = re.sub(r"\b(sk-|AIza)[A-Za-z0-9_\-]{6,}\b", "<redacted>", head)
    return head[:200]


async def _model_check(model: str) -> dict[str, Any]:
    """Cached per-model readiness check."""
    now = time.monotonic()
    cached = _readiness_cache.get(model)
    if cached is not None and now - cached[0] < _READINESS_TTL_S:
        return cached[1]
    result = await _ping_model(model)
    _readiness_cache[model] = (time.monotonic(), result)
    return result


def clear_readiness_cache() -> None:
    """Drop cached pings so the next readiness call re-checks (post-PATCH)."""
    _readiness_cache.clear()


def configured_providers(credential_keys: list[str]) -> list[str]:
    """LiteLLM providers unlocked by the currently-set credential keys."""
    seen: dict[str, None] = {}  # ordered de-dup
    for key in credential_keys:
        provider = provider_for_credential_key(key)
        if provider is not None:
            seen.setdefault(provider, None)
    return list(seen)


async def compute_readiness(credential_keys: list[str]) -> dict[str, Any]:
    """``ready`` iff both gate models pass a live 1-token pre-flight (cached)."""
    checks = await asyncio.gather(
        *(_model_check(_effective_model(key)) for _, key in _GATE_MODEL_KEYS)
    )
    models = {
        name: check for (name, _), check in zip(_GATE_MODEL_KEYS, checks, strict=True)
    }
    return {
        "ready": all(m["key_ok"] for m in models.values()),
        "credentials_set": len(credential_keys) > 0,
        "models": models,
    }


def _fetch_provider_models(provider: str) -> list[str]:
    """Live list for one provider with a static fallback. Blocking — run in a
    thread. Returns [] only if both live + static yield nothing."""
    models: list[str] = []
    if provider in LIVE_DISCOVERY_PROVIDERS:
        try:
            models = litellm.get_valid_models(
                check_provider_endpoint=True, custom_llm_provider=provider
            )
        except Exception:  # noqa: BLE001 — fall through to static
            models = []
    if not models:
        models = list(litellm.models_by_provider.get(provider, []))
    # LiteLLM mixes bare ("gemini-2.0-flash") and prefixed ("gemini/...") ids;
    # normalize every suggestion to the prefixed form the app expects.
    prefix = f"{provider}/"
    normalized = {m if "/" in m else f"{prefix}{m}" for m in models}
    return sorted(normalized)


async def fetch_model_options(providers: list[str]) -> dict[str, list[str]]:
    """Usable models per configured provider (cached, best-effort)."""
    now = time.monotonic()
    out: dict[str, list[str]] = {}
    stale = []
    for provider in providers:
        cached = _options_cache.get(provider)
        if cached is not None and now - cached[0] < _OPTIONS_TTL_S:
            out[provider] = cached[1]
        else:
            stale.append(provider)

    for provider in stale:
        try:
            models = await asyncio.wait_for(
                asyncio.to_thread(_fetch_provider_models, provider),
                timeout=_OPTIONS_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — timeout/network never breaks the panel
            models = list(_options_cache.get(provider, (0.0, []))[1])
        _options_cache[provider] = (time.monotonic(), models)
        out[provider] = models
    return out


def _reset_cache_for_tests() -> None:
    _options_cache.clear()
    _readiness_cache.clear()
