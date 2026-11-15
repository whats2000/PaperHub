"""Which configured LLM models run off-device (Plan H5a, task 10).

A model is *local* when its provider prefix is a self-hosted runtime (whose
base URL, if set, is a local host), or an
OpenAI-compatible endpoint whose ``*_API_BASE`` points at loopback, a private
range or an internal-looking host. Everything else (no prefix, ``gemini/``,
``anthropic/`` ...) counts as non-local. Readiness only warns about these.
"""
from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from urllib.parse import urlparse

_ALWAYS_LOCAL = frozenset({"ollama", "ollama_chat", "hosted_vllm", "lm_studio", "llamafile"})
# The base-URL credential each self-hosted runtime reads; unset = its local default.
_RUNTIME_BASE = {
    "ollama": "OLLAMA_API_BASE", "ollama_chat": "OLLAMA_API_BASE",
    "hosted_vllm": "HOSTED_VLLM_API_BASE", "lm_studio": "LM_STUDIO_API_BASE",
    "llamafile": "LLAMAFILE_API_BASE",
}
_COMPATIBLE = frozenset({"openai", "openai_compatible"})
_INTERNAL_SUFFIXES = (".local", ".internal", ".lan")
_SMALL_DEFAULT = "gemini/gemini-3.1-flash-lite"
_FLAGSHIP_DEFAULT = "gemini/gemini-2.5-pro"

# Per-slot model env keys and the tier each defaults to (config.load_settings).
_SLOT_TIERS: dict[str, str] = {
    "PAPERHUB_ROUTER_MODEL": "small",
    "PAPERHUB_CHITCHAT_MODEL": "small",
    "PAPERHUB_PAPER_QA_MODEL": "flagship",
    "PAPERHUB_PAPER_QA_SUBAGENT_MODEL": "small",
    "PAPERHUB_SQL_AGENT_MODEL": "small",
    "PAPERHUB_MEMORY_CONFLICT_MODEL": "small",
    "PAPERHUB_TRANSLATE_MODEL": "small",
    "PAPERHUB_FEEDBACK_REVIEW_MODEL": "flagship",
    "PAPERHUB_IDEATION_CARD_MODEL": "small",
    "PAPERHUB_IDEATION_MODEL": "flagship",
    "PAPERHUB_AGENT_MODEL": "flagship",
    "PAPERHUB_AGENT_GATHER_MODEL": "small",
    "PAPERHUB_AGENT_AUTOAPPROVE_MODEL": "small",
    "PAPERHUB_REPORT_PLAN_MODEL": "flagship",
    "PAPERHUB_REPORT_SECTION_MODEL": "flagship",
    "PAPERHUB_REPORT_NOTES_MODEL": "flagship",
    "PAPERHUB_REPORT_RESOLVE_MODEL": "small",
    "PAPERHUB_OUTBOUND_REVIEW_MODEL": "small",
}


def _local_host(host: str) -> bool:
    h = host.strip("[]").lower()
    if not h:
        return False
    if h in ("localhost", "host.docker.internal") or h.endswith(_INTERNAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _base_is_local(base: str) -> bool:
    parsed = urlparse(base if "//" in base else f"//{base}")
    return _local_host(parsed.hostname or "")


def _api_base_is_local(env: Mapping[str, str]) -> bool:
    for key in ("OPENAI_API_BASE", "OPENAI_BASE_URL"):
        base = env.get(key, "").strip()
        if base:
            parsed = urlparse(base if "//" in base else f"//{base}")
            return _local_host(parsed.hostname or "")
    return False


def is_local_model(model: str, env: Mapping[str, str]) -> bool:
    provider, sep, _ = model.strip().partition("/")
    if not sep:
        return False
    provider = provider.lower()
    if provider in _ALWAYS_LOCAL:
        # A self-hosted runtime is local unless its base URL (a credential
        # key, not a lockable setting) points at a public host.
        base = env.get(_RUNTIME_BASE.get(provider, ""), "").strip()
        return not base or _base_is_local(base)
    return provider in _COMPATIBLE and _api_base_is_local(env)


def marker_llm_model(env: Mapping[str, str]) -> str:
    """Same resolution as ``marker_service/llm_config.marker_llm_model`` (that
    package is a separate Docker project, not importable from the backend)."""
    explicit = env.get("PAPERHUB_MARKER_LLM_MODEL")
    if explicit:
        return explicit
    small = env.get("PAPERHUB_MODEL_SMALL", "")
    return small if small.startswith("gemini/") else _SMALL_DEFAULT


def configured_models(env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """``(key, effective model)`` for every model-bearing setting, one per key."""
    e = os.environ if env is None else env
    small = (e.get("PAPERHUB_MODEL_SMALL") or "").strip() or _SMALL_DEFAULT
    flagship = (e.get("PAPERHUB_MODEL_FLAGSHIP") or "").strip() or _FLAGSHIP_DEFAULT
    tiers = {"small": small, "flagship": flagship}
    out: dict[str, str] = {
        "PAPERHUB_MODEL_SMALL": small,
        "PAPERHUB_MODEL_FLAGSHIP": flagship,
    }
    for key, tier in _SLOT_TIERS.items():
        out[key] = (e.get(key) or "").strip() or tiers[tier]
    fb = (e.get("PAPERHUB_FEEDBACK_REVIEW_FALLBACK_MODEL") or "").strip()
    if fb:
        out["PAPERHUB_FEEDBACK_REVIEW_FALLBACK_MODEL"] = fb
    # The adapter's flagship -> small downgrade target is the small tier itself
    # (already listed). Marker's accuracy pass and the agent's page-image
    # reading (the agent model) also send content to a model.
    out["PAPERHUB_MARKER_LLM_MODEL"] = marker_llm_model(e)
    if (e.get("PAPERHUB_AGENT_VISION", "1")).strip().lower() in ("1", "true", "yes", "on"):
        out["PAPERHUB_AGENT_VISION"] = out["PAPERHUB_AGENT_MODEL"]
    return list(out.items())


def non_local_settings(env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    e = os.environ if env is None else env
    return [(k, m) for k, m in configured_models(e) if m and not is_local_model(m, e)]
