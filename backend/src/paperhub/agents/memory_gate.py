"""Deterministic safety gate + scope classifier for memory saves (SRS v2.17 FR-10).

classify_memory_safety runs BEFORE any memory.add (user-explicit via the memory
node AND agent-autonomous). Refuses sensitive (API keys, passwords, card/ID, medical,
salary) and dangerous (skip validation / disable security / ignore rules / bypass)
content. Rules own this boundary (§II-1 #3) — an LLM would have non-zero false-neg.

classify_memory_scope maps a fact to PaperHub's scope: `global` == user scope
(personal preferences/habits), `session` == project scope (project/framework/DB/arch
settings). FP#2 — the existing scope values carry the functional user/project distinction.

Credit-card detection note
--------------------------
The CC pattern matches the **grouped** wire format only (4-4-4-4 with spaces or dashes,
e.g. "4111 1111 1111 1111").  A bare run of 13–16 digits with no separators is NOT
matched — this is an intentional precision/recall tradeoff that stops large ML training-
token counts (e.g. 13000000000000) and ISBNs from triggering the pattern.
"""
from __future__ import annotations

import re
from typing import Literal

__all__ = ["MemoryGateRefusal", "classify_memory_safety", "classify_memory_scope"]


class MemoryGateRefusal(Exception):
    """Raised by callers that prefer an exception over checking the dict."""


# ---------------------------------------------------------------------------
# Sensitive patterns — order matters: most specific first.
#
# Password pattern: require an explicit assignment operator OR "is/was" so that
# "password policies", "password strength", "I forgot my password" etc. pass,
# while "password: secret123", "password=x", "my password is hunter2" refuse.
#
# Credit-card pattern: grouped format only (4-4-4-4 with spaces or dashes).
# This avoids false positives on bare long integers and ISBNs.
# ---------------------------------------------------------------------------
_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    # Anthropic / OpenAI API keys
    (r"\bsk-ant-[A-Za-z0-9\-_]{10,}", "API key (sk-ant-...)"),
    (r"\bsk-[A-Za-z0-9\-_]{10,}", "API key (sk-...)"),
    # Google API key
    (r"\bAIza[A-Za-z0-9\-_]{10,}", "API key (AIza...)"),
    # GitHub Personal Access Tokens
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub PAT (ghp_...)"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained PAT (github_pat_...)"),
    # HuggingFace token
    (r"\bhf_[A-Za-z0-9]{20,}", "HuggingFace token (hf_...)"),
    # Slack tokens (bot, app, user, workspace, etc.)
    (r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "Slack token (xox...)"),
    # PEM private key header (any key type)
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "PEM private key"),
    # JWT: three base64url segments separated by dots (header.payload.signature)
    (r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "JWT token"),
    # Password with explicit assignment or is/was — NOT bare topic mentions
    (r"\bpassword\s*[:=]\s*\S+", "password"),
    (r"\bpassword\s+(?:is|was)\s+\S+", "password"),
    (r"\bpasswd\s*[:=]\s*\S+", "password"),
    (r"\bpasswd\s+(?:is|was)\s+\S+", "password"),
    # Credit card — grouped format only (4-4-4-4 Visa/MC/Discover or 4-6-5 Amex)
    (r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b", "credit card number"),
    (r"\b\d{4}[ -]\d{6}[ -]\d{5}\b", "credit card number (Amex)"),
    # Social security / national ID
    (r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b", "ID number"),
    # Medical / PII keywords
    (r"\b(?:diagnosed|diagnosis|medical record|patient id|PHI)\b", "medical/PII"),
    # Salary disclosure
    (r"\bsalary\s+(?:is|was|of)\s+[\$\d]", "salary"),
]

_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\bskip\s+validation\b", "dangerous: skip validation"),
    (r"\bdisable\s+security\b", "dangerous: disable security"),
    (r"\bignore\s+(?:the\s+)?rules?\b", "dangerous: ignore rules"),
    (r"\bbypass\s+(?:security|review|checks?|validation)\b", "dangerous: bypass"),
    (r"\bdisable\s+(?:the\s+)?(?:check|guard|filter|review)\b", "dangerous: disable check"),
]


def classify_memory_safety(text: str) -> dict[str, object]:
    for pattern, label in _SENSITIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return {
                "save": False,
                "risk": "sensitive",
                "reason": (
                    f"Content matches a sensitive-data pattern ({label}). "
                    "PaperHub does not store API keys, passwords, PII, or similar."
                ),
            }
    for pattern, label in _DANGEROUS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return {
                "save": False,
                "risk": "dangerous",
                "reason": (
                    f"Content matches a dangerous-instruction pattern ({label}). "
                    "Instructions to bypass safety or ignore validation cannot be stored."
                ),
            }
    return {"save": True, "risk": "", "reason": ""}


# ---------------------------------------------------------------------------
# Scope classifier
#
# 'use ' and 'uses ' are intentionally NOT in _PROJECT_KEYWORDS — they are too
# broad and misroute personal habits ("I use Python daily", "use dark mode") to
# session scope.  Project-specific tech tokens (flask, fastapi, etc.) are kept.
# ---------------------------------------------------------------------------
_PROJECT_KEYWORDS = (
    "this project",
    "本專案",
    "這個專案",
    "architecture",
    "架構",
    "database",
    "資料庫",
    "framework",
    "框架",
    "flask",
    "fastapi",
    "mysql",
    "postgresql",
)
_PREFERENCE_KEYWORDS = (
    "always",
    "every time",
    "prefer",
    "i want",
    "以後",
    "每次",
    "都用",
    "偏好",
    "不要",
    "習慣",
    "請固定",
    "預設",
)


def classify_memory_scope(text: str) -> Literal["session", "global"]:
    """Rule-based scope: project-setting → 'session' (project scope);
    personal preference → 'global' (user scope). Default 'global'."""
    lower = text.lower()
    if any(k in lower for k in _PROJECT_KEYWORDS):
        return "session"
    if any(k in lower for k in _PREFERENCE_KEYWORDS):
        return "global"
    return "global"
