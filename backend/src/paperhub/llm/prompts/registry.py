from dataclasses import dataclass
from importlib.resources import files

import yaml


@dataclass(frozen=True)
class PromptSlot:
    system: str
    user_template: str


class PromptRegistry:
    def __init__(self) -> None:
        self._cache: dict[str, PromptSlot] = {}

    def get(self, slot: str) -> PromptSlot:
        if slot in self._cache:
            return self._cache[slot]
        name, _, version = slot.partition("/")
        if not version:
            raise ValueError(f"prompt slot must be 'name/version', got {slot!r}")
        path = files("paperhub.llm.prompts") / f"{name}_{version}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        result = PromptSlot(system=data["system"], user_template=data["user"])
        self._cache[slot] = result
        return result
