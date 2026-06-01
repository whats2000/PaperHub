"""F4.4 T10 — yaml-driven layout-example library for the slide renderer.

This module exists to kill the per-``pattern_kind`` template-fill
anti-pattern that the renderer accumulated through T3-T9. Each entry
in ``slide_layout_examples.yaml`` is a DESIGN IDEA — a purpose, a
when-to-use rationale, and an illustrative LaTeX example — that the
renderer feeds to the LLM as a reference. The LLM REASONS about
which layout serves the slide's content + goal, picks and adapts an
example, OR designs a fresh layout if the library doesn't cover the
case. No surface-form template enforcement.

The data is data-only: this module loads the yaml + exposes a frozen
dataclass + a list helper. The composition into a prompt body lives
in ``sl_render_slide``.

Mirrors the pattern of
``backend/src/paperhub/pipelines/slide_pipeline/style_profile.py``
(yaml load via ``importlib.resources``, frozen dataclass, defensive
on missing fields, schema-drift caught at load time).
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import yaml

__all__ = [
    "LayoutExample",
    "list_layout_ids",
    "load_layout_examples",
]


@dataclass(frozen=True)
class LayoutExample:
    """One layout idea in the renderer's design-reference library.

    See ``slide_layout_examples.yaml`` for the canonical contents and
    the comment block at the top of that file for operator-facing
    notes on editing.
    """

    id: str
    """Unique identifier — slug-cased (e.g. ``"references_three_column"``)."""

    purpose: str
    """One-line summary of the design idea."""

    when_to_use: str
    """Reasoning about when this layout serves the content. The
    renderer prompt embeds this verbatim so the LLM can decide
    whether the example applies to the slide it's designing.
    """

    example: str
    """Hand-authored LaTeX example illustrating the layout. NOT a
    template the LLM must fill — just an instance of the concept
    rendered, for inspiration.
    """


# Known top-level keys in the yaml. Anything else raises at load time
# so a typo'd field doesn't silently disappear.
_KNOWN_TOP_LEVEL_KEYS = {"version", "layouts"}
_KNOWN_LAYOUT_KEYS = {"id", "purpose", "when_to_use", "example"}


_CACHE: list[LayoutExample] | None = None


def _load_yaml_data() -> dict[str, Any]:
    path = files("paperhub.agents") / "slide_layout_examples.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            "slide_layout_examples.yaml must be a mapping with top-level "
            "keys 'version' and 'layouts'"
        )
    unknown_keys = set(data.keys()) - _KNOWN_TOP_LEVEL_KEYS
    if unknown_keys:
        raise ValueError(
            "slide_layout_examples.yaml has unknown top-level "
            f"key(s) {sorted(unknown_keys)!r}; expected only "
            f"{sorted(_KNOWN_TOP_LEVEL_KEYS)!r}. Schema drift — add the "
            "field to _KNOWN_TOP_LEVEL_KEYS deliberately if you mean it."
        )
    return data


def _parse_layout(spec: Any, *, index: int) -> LayoutExample:
    if not isinstance(spec, dict):
        raise ValueError(
            f"slide_layout_examples.yaml: layouts[{index}] must be a "
            f"mapping, got {type(spec).__name__}"
        )
    unknown = set(spec.keys()) - _KNOWN_LAYOUT_KEYS
    if unknown:
        raise ValueError(
            f"slide_layout_examples.yaml: layouts[{index}] has unknown "
            f"key(s) {sorted(unknown)!r}; expected only "
            f"{sorted(_KNOWN_LAYOUT_KEYS)!r}"
        )

    def _str(key: str) -> str:
        value = spec.get(key)
        if value is None:
            raise ValueError(
                f"slide_layout_examples.yaml: layouts[{index}] missing "
                f"required field {key!r}"
            )
        if not isinstance(value, str):
            raise ValueError(
                f"slide_layout_examples.yaml: layouts[{index}].{key} "
                f"must be a string, got {type(value).__name__}"
            )
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                f"slide_layout_examples.yaml: layouts[{index}].{key} "
                "must be a non-empty string"
            )
        # Preserve internal newlines (LaTeX examples need them), only
        # strip outer whitespace.
        return value.strip("\n").rstrip()

    return LayoutExample(
        id=_str("id"),
        purpose=_str("purpose"),
        when_to_use=_str("when_to_use"),
        example=_str("example"),
    )


def load_layout_examples() -> list[LayoutExample]:
    """Load the full layout-example library in registry order.

    Caches the parsed result. Raises ``ValueError`` on schema drift
    (unknown keys, missing fields, empty fields, wrong types) so a
    bad edit fails fast instead of producing a silently-degraded
    library.
    """
    global _CACHE
    if _CACHE is not None:
        return list(_CACHE)
    data = _load_yaml_data()
    raw_layouts = data.get("layouts")
    if not isinstance(raw_layouts, list) or not raw_layouts:
        raise ValueError(
            "slide_layout_examples.yaml must declare a non-empty "
            "'layouts' list"
        )
    parsed: list[LayoutExample] = []
    seen_ids: set[str] = set()
    for idx, spec in enumerate(raw_layouts):
        entry = _parse_layout(spec, index=idx)
        if entry.id in seen_ids:
            raise ValueError(
                f"slide_layout_examples.yaml: duplicate layout id "
                f"{entry.id!r} at layouts[{idx}]"
            )
        seen_ids.add(entry.id)
        parsed.append(entry)
    _CACHE = parsed
    return list(parsed)


def list_layout_ids() -> list[str]:
    """Return every layout id in registry order."""
    return [layout.id for layout in load_layout_examples()]
