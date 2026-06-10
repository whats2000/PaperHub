# backend/src/paperhub/settings_registry.py
"""Declarative registry of editable .env-class settings (Plan G / FR-14).

The registry is the single source of truth for what the Settings panel can
edit, how each field is validated, and which fields are secrets or require a
restart. Provider credentials are NOT enumerated here — they are a free-form
category guarded by ``is_allowed_credential_key``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

FieldType = Literal["string", "int", "bool", "email", "enum", "secret"]

# Domain labels exclude '.' so the dot separators are unambiguous — this avoids
# the polynomial-backtracking overlap CodeQL flags (py/polynomial-redos) when
# two adjacent '.'-matching quantifiers straddle a literal '\.'.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")


@dataclass(frozen=True)
class SettingField:
    key: str
    category: str
    label: str
    type: FieldType
    default: str | None = None
    help: str = ""
    secret: bool = False
    restart_required: bool = False
    read_only: bool = False
    # Seldom-configured fields (e.g. per-slot model overrides) the UI tucks
    # under a collapsed "advanced" disclosure instead of showing inline.
    advanced: bool = False
    min: int | None = None
    max: int | None = None
    choices: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    # Optional "where to get this" link (e.g. a provider's API-key page), shown
    # under the field. The frontend localizes the link label, not the URL.
    docs_url: str = ""
    # Optional sub-group key within a category — the panel renders a heading per
    # contiguous group (the frontend localizes the title). Empty = ungrouped.
    group: str = ""


# Curated set of known LiteLLM provider env vars (offered as autocomplete in
# the free-form credentials editor). New providers also work via the suffix
# pattern in ``is_allowed_credential_key`` — extend this list to add a
# suggestion, not to unlock a provider.
PROVIDER_CREDENTIAL_SUGGESTIONS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_API_KEY",
    "AZURE_API_BASE",
    "AZURE_API_VERSION",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "TOGETHERAI_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITYAI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "VERTEXAI_PROJECT",
    "VERTEXAI_LOCATION",
    "OLLAMA_API_BASE",
)

# A free-form credential key is accepted if it is a known suggestion OR matches
# the credential-shaped suffix pattern. This blocks arbitrary env injection
# (PATH, HOME, …) while letting any real provider env var through.
_CREDENTIAL_SUFFIX_RE = re.compile(
    r"^(?!PAPERHUB_)[A-Z][A-Z0-9_]*_(API_KEY|API_BASE|API_VERSION|KEY|TOKEN|REGION|PROJECT|LOCATION)$"
)


def is_allowed_credential_key(key: str) -> bool:
    return key in PROVIDER_CREDENTIAL_SUGGESTIONS or bool(_CREDENTIAL_SUFFIX_RE.match(key))


# Map a credential env-var key -> the LiteLLM provider it unlocks. Used to derive
# the configured-provider set for live model discovery. Keys whose provider name
# doesn't match the lowercased prefix (together_ai, perplexity, vertex_ai) are
# listed explicitly; unlisted *_API_KEY keys fall back to the lowercased prefix.
_CREDENTIAL_KEY_TO_PROVIDER: dict[str, str] = {
    "GEMINI_API_KEY": "gemini",
    "OPENAI_API_KEY": "openai",
    "ANTHROPIC_API_KEY": "anthropic",
    "AZURE_API_KEY": "azure",
    "OPENROUTER_API_KEY": "openrouter",
    "MISTRAL_API_KEY": "mistral",
    "GROQ_API_KEY": "groq",
    "COHERE_API_KEY": "cohere",
    "DEEPSEEK_API_KEY": "deepseek",
    "TOGETHERAI_API_KEY": "together_ai",
    "XAI_API_KEY": "xai",
    "PERPLEXITYAI_API_KEY": "perplexity",
    "GOOGLE_APPLICATION_CREDENTIALS": "vertex_ai",
    "VERTEXAI_PROJECT": "vertex_ai",
}

# Providers whose models LiteLLM can fetch live from the provider's own API
# (``get_valid_models(check_provider_endpoint=True)``). Others fall back to the
# bundled static model map. See docs.litellm.ai/docs/proxy/model_discovery.
LIVE_DISCOVERY_PROVIDERS: frozenset[str] = frozenset(
    {"gemini", "openai", "anthropic", "xai", "vertex_ai", "fireworks_ai", "vllm", "topaz"}
)


# Reverse of the *_API_KEY entries above: provider -> its primary key env var.
# Lets the readiness check name the key a model needs even when LiteLLM reports
# an empty-valued env var as "present" (so we can flag it as actually missing).
PROVIDER_PRIMARY_KEY: dict[str, str] = {
    provider: key
    for key, provider in _CREDENTIAL_KEY_TO_PROVIDER.items()
    if key.endswith("_API_KEY")
}


def primary_key_for_model(model: str) -> str | None:
    """The primary API-key env var for a ``provider/model`` id, if known."""
    provider = model.split("/", 1)[0] if "/" in model else None
    return PROVIDER_PRIMARY_KEY.get(provider) if provider else None


def provider_for_credential_key(key: str) -> str | None:
    """The LiteLLM provider a credential key unlocks, or None for non-key creds
    (config-only keys like AZURE_API_BASE that don't map to a provider list)."""
    if key in _CREDENTIAL_KEY_TO_PROVIDER:
        return _CREDENTIAL_KEY_TO_PROVIDER[key]
    m = re.match(r"^([A-Z][A-Z0-9]*)_API_KEY$", key)
    return m.group(1).lower() if m else None


_SMALL = "gemini/gemini-3.1-flash-lite"
_FLAGSHIP = "gemini/gemini-2.5-pro"

SETTINGS_REGISTRY: list[SettingField] = [
    # ── LLM model selection ─────────────────────────────────────────────
    SettingField("PAPERHUB_MODEL_SMALL", "models_providers", "Small-tier model", "string",
                 default=_SMALL, help="Default for classifiers / fast tool calls."),
    SettingField("PAPERHUB_MODEL_FLAGSHIP", "models_providers", "Flagship-tier model", "string",
                 default=_FLAGSHIP, help="Default for user-facing prose."),
    # Per-slot overrides (advanced; default to one of the two tiers above).
    SettingField("PAPERHUB_ROUTER_MODEL", "models_providers", "Router model", "string",
                 advanced=True,
                 help="Picks the intent for each turn (chitchat / paper_search / paper_qa / slides …). Defaults to the small tier."),
    SettingField("PAPERHUB_CHITCHAT_MODEL", "models_providers", "Chitchat model", "string",
                 advanced=True,
                 help="Small-talk replies when the router picks chitchat. Defaults to the small tier."),
    SettingField("PAPERHUB_PAPER_QA_MODEL", "models_providers", "paper_qa finalizer", "string",
                 advanced=True,
                 help="Cross-paper answer synthesis, streamed to you. Defaults to the flagship tier."),
    SettingField("PAPERHUB_PAPER_QA_SUBAGENT_MODEL", "models_providers", "paper_qa subagent", "string",
                 advanced=True,
                 help="Per-paper section navigation + chunk selection. Defaults to the small tier."),
    SettingField("PAPERHUB_SQL_AGENT_MODEL", "models_providers", "SQL planner", "string",
                 advanced=True,
                 help="library_stats NL→SQL planning + self-repair. Defaults to the small tier."),
    SettingField("PAPERHUB_SQL_ANSWER_MODEL", "models_providers", "SQL answer", "string",
                 advanced=True,
                 help="library_stats natural-language answer phrasing. Defaults to the flagship tier."),
    SettingField("PAPERHUB_MEMORY_CONFLICT_MODEL", "models_providers", "Memory conflict detector", "string",
                 advanced=True,
                 help="Checks whether a new memory contradicts an existing one. Defaults to the small tier."),
    SettingField("PAPERHUB_REPORT_RESOLVE_MODEL", "models_providers", "Slide resolver", "string",
                 advanced=True,
                 help="Resolves enabled papers + classifies deck commands. Defaults to the small tier."),
    SettingField("PAPERHUB_REPORT_NOTES_MODEL", "models_providers", "Slide notes author", "string",
                 advanced=True,
                 help="Writes the deck's speaker notes. Defaults to the flagship tier."),
    SettingField("PAPERHUB_REPORT_PLAN_MODEL", "models_providers", "Slide agent", "string",
                 advanced=True,
                 help="Per-paper gather-context + the slide agent. Defaults to the flagship tier."),
    SettingField("PAPERHUB_REPORT_SECTION_MODEL", "models_providers", "Slide single-frame edit", "string",
                 advanced=True,
                 help="Single-frame slide / title / preamble edits. Defaults to the flagship tier."),
    # ── Agent tunables ──────────────────────────────────────────────────
    SettingField("PAPERHUB_PAPER_QA_MAX_SECTION_READS", "agents_memory",
                 "Max section reads / subagent turn", "int", default="8", min=1, max=50),
    SettingField("PAPERHUB_SESSION_RETENTION_DAYS", "agents_memory",
                 "Soft-deleted session retention (days)", "int", default="30", min=1, max=3650),
    # ── Memory / recall ─────────────────────────────────────────────────
    SettingField("PAPERHUB_MEMORY_RECALL", "agents_memory", "Inject recalled memories", "bool",
                 default="1", help="Surface active memories to answering agents."),
    # NOTE: PAPERHUB_MEMORY_SEMANTIC is intentionally OMITTED — dead config.
    # ── External services ───────────────────────────────────────────────
    SettingField("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY", "integrations",
                 "Semantic Scholar API key", "secret", secret=True, group="paper_sources",
                 help="Optional but recommended — speeds up paper search. The "
                      "unauthenticated tier is heavily rate-limited (it won't "
                      "block the app, just slow searches).",
                 docs_url="https://www.semanticscholar.org/product/api#api-key"),
    # ── External lookup ─────────────────────────────────────────────────
    SettingField("PAPERHUB_UNPAYWALL_EMAIL", "integrations", "Unpaywall contact email", "email",
                 group="paper_sources",
                 help="Enables the DOI→free-PDF fallback. Used for abuse logging only."),
    # ── Storage ─────────────────────────────────────────────────────────
    SettingField("PAPERHUB_MAX_UPLOAD_MB", "system", "Max PDF upload (MiB)", "int",
                 default="30", min=1, max=500),
    SettingField("PAPERHUB_WORKSPACE", "system", "Workspace directory", "string",
                 default="./workspace", restart_required=True, read_only=True,
                 help="Set via env var at boot; restart the backend to change."),
    # ── Logging ─────────────────────────────────────────────────────────
    SettingField("PAPERHUB_LOG_LEVEL", "system", "Log level", "enum", default="INFO",
                 restart_required=True, choices=("DEBUG", "INFO", "WARNING", "ERROR")),
    # ── Marker (PDF ingestion) — grouped together at the bottom ──────────
    SettingField("PAPERHUB_MARKER_MAX_PAGES", "integrations",
                 "Marker pages per /extract call", "int", default="1", min=1, max=100,
                 restart_required=True, group="marker"),
    SettingField("PAPERHUB_MARKER_URL", "integrations", "Marker service URL", "string",
                 default="http://127.0.0.1:8002", restart_required=True, group="marker"),
    SettingField("PAPERHUB_INPROCESS_MARKER", "integrations", "In-process Marker", "bool",
                 default="0", restart_required=True, group="marker"),
    # ── Slides ──────────────────────────────────────────────────────────
    SettingField("PAPERHUB_SLIDE_STYLE_PROFILE", "system", "Slide style profile", "enum",
                 default="default", choices=("default", "metropolis_minimal")),
]

_BY_KEY = {f.key: f for f in SETTINGS_REGISTRY}


def field_by_key(key: str) -> SettingField | None:
    return _BY_KEY.get(key)


def coerce_value(field: SettingField, raw: str) -> str:
    """Validate ``raw`` against ``field`` and return the canonical string to
    store. Raises ``ValueError`` on invalid input."""
    if field.read_only:
        raise ValueError(f"{field.key} is read-only (set it via env at boot).")
    value = raw.strip()
    if field.type == "int":
        try:
            n = int(value)
        except ValueError as exc:
            raise ValueError(f"{field.key} must be an integer.") from exc
        if field.min is not None and n < field.min:
            raise ValueError(f"{field.key} must be >= {field.min}.")
        if field.max is not None and n > field.max:
            raise ValueError(f"{field.key} must be <= {field.max}.")
        return str(n)
    if field.type == "bool":
        if value.lower() in ("1", "true", "yes", "on"):
            return "1"
        if value.lower() in ("0", "false", "no", "off"):
            return "0"
        raise ValueError(f"{field.key} must be a boolean (0/1).")
    if field.type == "enum":
        if value not in field.choices:
            raise ValueError(f"{field.key} must be one of: {', '.join(field.choices)}.")
        return value
    if field.type == "email":
        if not _EMAIL_RE.match(value):
            raise ValueError(f"{field.key} must be a valid email address.")
        return value
    # string / secret
    if not value:
        raise ValueError(f"{field.key} must not be empty.")
    return value
