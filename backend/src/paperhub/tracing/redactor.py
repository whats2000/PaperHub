import os
import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{1,}"), "<redacted:anthropic>"),
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{1,}"), "<redacted:openai>"),
    (re.compile(r"AIza[A-Za-z0-9_-]{10,}"), "<redacted:google>"),
]


def _home_paths() -> list[str]:
    paths: list[str] = []
    for env in ("HOME", "USERPROFILE"):
        value = os.environ.get(env)
        if value:
            paths.append(value)
    return paths


def _redact_str(value: str) -> str:
    for env_path in _home_paths():
        value = value.replace(env_path, "$HOME")
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    return value
