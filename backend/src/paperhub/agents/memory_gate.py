"""Deterministic safety gate + scope classifier for memory saves (SRS v2.17 FR-10).

classify_memory_safety runs BEFORE any memory.add (user-explicit via the memory
node AND agent-autonomous). Refuses sensitive (API keys, passwords, card/ID, medical,
salary) and dangerous (skip validation / disable security / ignore rules / bypass)
content. Rules own this boundary (§II-1 #3) — an LLM would have non-zero false-neg.

classify_memory_scope maps a fact to PaperHub's scope: `global` == user scope
(personal preferences/habits), `session` == project scope (project/framework/DB/arch
settings). FP#2 — the existing scope values carry the functional user/project distinction.
"""
from __future__ import annotations

import re
from typing import Literal

__all__ = ["MemoryGateRefusal", "classify_memory_safety", "classify_memory_scope"]


class MemoryGateRefusal(Exception):
    """Raised by callers that prefer an exception over checking the dict."""


_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\bsk-ant-[A-Za-z0-9\-_]{10,}", "API key (sk-ant-...)"),
    (r"\bsk-[A-Za-z0-9\-_]{10,}", "API key (sk-...)"),
    (r"\bAIza[A-Za-z0-9\-_]{10,}", "API key (AIza...)"),
    (r"\bpassword\s*[:=\s]\s*\S+", "password"),
    (r"\bpasswd\s*[:=\s]\s*\S+", "password"),
    (r"\b(?:\d[ -]?){13,16}\b", "credit card number"),
    (r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b", "ID number"),
    (r"\b(?:diagnosed|diagnosis|medical record|patient id|PHI)\b", "medical/PII"),
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
    "uses ",
    "use ",
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
