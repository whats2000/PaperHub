from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReplayOutput:
    output: dict[str, Any]
    tokens_in: int | None
    error: str | None = None
