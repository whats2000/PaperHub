"""Secret + path redaction for the tool-call audit log (NFR-09).

`redact(payload)` recursively walks a JSON-shaped value and replaces:
  - Anthropic / OpenAI API key shapes → "<REDACTED:api-key>"
  - Absolute paths under the user's home dir → "<REDACTED:home>"

Over-eager rather than under-eager — better to obscure a non-secret than
to leak a real one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

_API_KEY_RE = re.compile(r"sk-(?:ant|proj)-[A-Za-z0-9_\-]{8,}")
_HOME_PREFIX = str(Path.home())


def _scrub_string(s: str) -> str:
    redacted = _API_KEY_RE.sub("<REDACTED:api-key>", s)
    if _HOME_PREFIX and _HOME_PREFIX in redacted:
        redacted = redacted.replace(_HOME_PREFIX, "<REDACTED:home>")
    return redacted


def _scrub(value: object) -> object:
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in cast(dict[str, object], value).items()}
    if isinstance(value, list):
        return [_scrub(v) for v in cast(list[object], value)]
    return value


def redact(payload: dict[str, object]) -> dict[str, object]:
    """Return a redacted copy of `payload`. Input is not mutated."""
    return {k: _scrub(v) for k, v in payload.items()}
