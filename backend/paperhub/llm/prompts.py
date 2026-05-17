"""YAML-driven prompt registry.

All prompts live in prompts.yaml. Slots are addressable as <slot>.<version>;
later phases add slots and additional versions for A/B work. Templates
render with str.format.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import cast

import yaml


class PromptNotFoundError(KeyError):
    """Raised when a (slot, version) pair is missing from the registry."""


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str


class PromptRegistry:
    def __init__(self, data: dict[str, dict[str, dict[str, str]]]) -> None:
        self._data = data

    @classmethod
    def load_default(cls) -> PromptRegistry:
        text = resources.files("paperhub.llm").joinpath("prompts.yaml").read_text(encoding="utf-8")
        loaded = cast(dict[str, dict[str, dict[str, str]]], yaml.safe_load(text))
        return cls(loaded)

    def render(self, *, slot: str, version: str, **vars: object) -> RenderedPrompt:
        slot_entry = self._data.get(slot)
        if slot_entry is None:
            raise PromptNotFoundError(f"slot {slot!r} not in registry")
        version_entry = slot_entry.get(version)
        if version_entry is None:
            raise PromptNotFoundError(f"version {version!r} of slot {slot!r} not in registry")
        system = version_entry["system"]
        template = version_entry["user_template"]
        return RenderedPrompt(system=system, user=template.format(**vars))
