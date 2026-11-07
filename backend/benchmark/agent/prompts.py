"""Eval-only prompt-variant store: a browsable folder of YAML files (§III-9).

Experimental prompt variants live in ``<prompts_dir>/<stage>/<version>.yaml``
(``system:`` + ``user:`` blocks) so a human or Claude can open, read, and edit a
variant to "experience the performance". This is SEPARATE from the production
registry under ``src/.../llm/prompts/`` — the eval never touches deploy code.
The baseline ``router/v1.yaml`` is seeded as a copy of the shipped prompt;
adopting a winner = copy its YAML back into the registry (a separate step).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROMPTS_DIR = "benchmark/agent/prompts"


def load_variant(stage: str, version: str, *, prompts_dir: str | Path) -> tuple[str, str]:
    path = Path(prompts_dir) / stage / f"{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"prompt variant not found: {path} — create it (system:/user: blocks) "
            f"or seed it from the registry's {stage}_{version}.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return str(data["system"]), str(data["user"])


def load_variant_params(stage: str, version: str, *, prompts_dir: str | Path) -> dict[str, Any]:
    """The variant's optional ``params:`` mapping — extra provider kwargs for
    every call (e.g. ``reasoning_effort: low``), so a call setting is swept
    like a prompt variant. ``{}`` when the YAML has none."""
    path = Path(prompts_dir) / stage / f"{version}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    params = data.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"{path}: params must be a mapping")
    return dict(params)


def list_variants(stage: str, *, prompts_dir: str | Path) -> list[str]:
    d = Path(prompts_dir) / stage
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))
