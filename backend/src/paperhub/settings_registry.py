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

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    min: int | None = None
    max: int | None = None
    choices: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


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
    r"^[A-Z][A-Z0-9_]*_(API_KEY|API_BASE|API_VERSION|KEY|TOKEN|REGION|PROJECT|LOCATION)$"
)


def is_allowed_credential_key(key: str) -> bool:
    return key in PROVIDER_CREDENTIAL_SUGGESTIONS or bool(_CREDENTIAL_SUFFIX_RE.match(key))


_SMALL = "gemini/gemini-3.1-flash-lite"
_FLAGSHIP = "gemini/gemini-2.5-pro"

SETTINGS_REGISTRY: list[SettingField] = [
    # ── LLM model selection ─────────────────────────────────────────────
    SettingField("PAPERHUB_MODEL_SMALL", "llm_models", "Small-tier model", "string",
                 default=_SMALL, help="Default for classifiers / fast tool calls."),
    SettingField("PAPERHUB_MODEL_FLAGSHIP", "llm_models", "Flagship-tier model", "string",
                 default=_FLAGSHIP, help="Default for user-facing prose."),
    SettingField("PAPERHUB_ROUTER_MODEL", "llm_models", "Router model", "string",
                 help="Intent classifier. Defaults to the small tier."),
    SettingField("PAPERHUB_CHITCHAT_MODEL", "llm_models", "Chitchat model", "string"),
    SettingField("PAPERHUB_PAPER_QA_MODEL", "llm_models", "paper_qa finalizer", "string"),
    SettingField("PAPERHUB_PAPER_QA_SUBAGENT_MODEL", "llm_models", "paper_qa subagent", "string"),
    SettingField("PAPERHUB_SQL_AGENT_MODEL", "llm_models", "SQL planner", "string"),
    SettingField("PAPERHUB_SQL_ANSWER_MODEL", "llm_models", "SQL answer", "string"),
    SettingField("PAPERHUB_MEMORY_CONFLICT_MODEL", "llm_models", "Memory conflict detector", "string"),
    SettingField("PAPERHUB_REPORT_RESOLVE_MODEL", "llm_models", "Slide resolver", "string"),
    SettingField("PAPERHUB_REPORT_NOTES_MODEL", "llm_models", "Slide notes author", "string"),
    SettingField("PAPERHUB_REPORT_PLAN_MODEL", "llm_models", "Slide agent", "string"),
    SettingField("PAPERHUB_REPORT_SECTION_MODEL", "llm_models", "Slide single-frame edit", "string"),
    # ── Agent tunables ──────────────────────────────────────────────────
    SettingField("PAPERHUB_PAPER_QA_MAX_SECTION_READS", "agent_tunables",
                 "Max section reads / subagent turn", "int", default="8", min=1, max=50),
    SettingField("PAPERHUB_SESSION_RETENTION_DAYS", "agent_tunables",
                 "Soft-deleted session retention (days)", "int", default="30", min=1, max=3650),
    SettingField("PAPERHUB_MARKER_MAX_PAGES", "marker",
                 "Marker pages per /extract call", "int", default="1", min=1, max=100),
    # ── Memory / recall ─────────────────────────────────────────────────
    SettingField("PAPERHUB_MEMORY_RECALL", "memory", "Inject recalled memories", "bool",
                 default="1", help="Surface active memories to answering agents."),
    # NOTE: PAPERHUB_MEMORY_SEMANTIC is intentionally OMITTED — dead config.
    # ── External services ───────────────────────────────────────────────
    SettingField("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY", "external_services",
                 "Semantic Scholar API key", "secret", secret=True,
                 help="Optional; the unauthenticated tier is rate-limited."),
    # ── External lookup ─────────────────────────────────────────────────
    SettingField("PAPERHUB_UNPAYWALL_EMAIL", "external_lookup", "Unpaywall contact email", "email",
                 help="Enables the DOI→free-PDF fallback. Used for abuse logging only."),
    # ── Storage ─────────────────────────────────────────────────────────
    SettingField("PAPERHUB_MAX_UPLOAD_MB", "storage", "Max PDF upload (MiB)", "int",
                 default="30", min=1, max=500),
    SettingField("PAPERHUB_WORKSPACE", "storage", "Workspace directory", "string",
                 default="./workspace", restart_required=True, read_only=True,
                 help="Set via env var at boot; restart the backend to change."),
    # ── Logging ─────────────────────────────────────────────────────────
    SettingField("PAPERHUB_LOG_LEVEL", "logging", "Log level", "enum", default="INFO",
                 restart_required=True, choices=("DEBUG", "INFO", "WARNING", "ERROR")),
    # ── Marker ──────────────────────────────────────────────────────────
    SettingField("PAPERHUB_MARKER_URL", "marker", "Marker service URL", "string",
                 default="http://127.0.0.1:8002", restart_required=True),
    SettingField("PAPERHUB_INPROCESS_MARKER", "marker", "In-process Marker", "bool",
                 default="0", restart_required=True),
    # ── Slides ──────────────────────────────────────────────────────────
    SettingField("PAPERHUB_SLIDE_STYLE_PROFILE", "slides", "Slide style profile", "enum",
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
            raise ValueError(f"{field.key} must be one of {field.choices}.")
        return value
    if field.type == "email":
        if not _EMAIL_RE.match(value):
            raise ValueError(f"{field.key} must be a valid email address.")
        return value
    # string / secret
    if not value:
        raise ValueError(f"{field.key} must not be empty.")
    return value
