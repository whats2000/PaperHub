"""Pick Marker's LLM service from the PaperHub model config (no torch import).

``PAPERHUB_MARKER_LLM_MODEL`` is a LiteLLM-style ``provider/model`` id. Unset,
it inherits the backend's ``PAPERHUB_MODEL_SMALL`` only when that is a
``gemini/`` model (a small ollama/openai model is often text-only, but Marker
needs vision), else Gemini flash-lite. ollama / openai / hosted_vllm are used
only when set explicitly.
Returns Marker 1.10.2 config keys, or ``None`` when no credential is usable
(keyless runs keep working, just without the LLM pass).
"""

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite"


def marker_llm_model(env: Mapping[str, str]) -> str:
    explicit = env.get("PAPERHUB_MARKER_LLM_MODEL")
    if explicit:
        return explicit
    small = env.get("PAPERHUB_MODEL_SMALL", "")
    return small if small.startswith("gemini/") else DEFAULT_MODEL


def marker_llm_config(env: Mapping[str, str]) -> dict[str, str] | None:
    provider, _, model = marker_llm_model(env).partition("/")
    if not model:
        return None
    if provider == "gemini":
        key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
        if not key:
            return None
        return {
            "llm_service": "marker.services.gemini.GoogleGeminiService",
            "gemini_api_key": key,
            "gemini_model_name": model,
        }
    if provider == "ollama":
        return {
            "llm_service": "marker.services.ollama.OllamaService",
            "ollama_base_url": env.get("OLLAMA_API_BASE") or "http://localhost:11434",
            "ollama_model": model,
        }
    if provider in ("openai", "hosted_vllm"):
        cfg = {
            "llm_service": "marker.services.openai.OpenAIService",
            "openai_api_key": env.get("OPENAI_API_KEY") or "none",
            "openai_model": model,
        }
        base = env.get("OPENAI_API_BASE")
        if base:  # unset → Marker's own default, never an empty URL
            cfg["openai_base_url"] = base
        return cfg
    logger.warning(
        "Marker LLM model %r has an unsupported provider (need gemini/, ollama/, "
        "openai/ or hosted_vllm/); inline-math recovery is off",
        marker_llm_model(env),
    )
    return None
