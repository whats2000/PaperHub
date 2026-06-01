"""F4.5 layout-example library loader.

Each example is an INSPIRATION reference for the slide_agent, not a
template to fill. The agent reads ``purpose`` + ``when_to_use`` +
``matches_aspect`` to pick a layout that fits the figure aspect + the
slide's content; it may also DESIGN a custom layout the library does
not cover.

v2.25 / F4.5: title + closer are folded into normal-frame examples — no
separate visual family. The agent uses ``\\begin{frame}{Title}`` for
both, with the regular theme's headline/footline. The old F4.4 R1
``closer_*`` family (5 ``[plain]`` + ``\\Large`` + ``\\rule`` variants)
and the ``title_simple`` ``[plain]`` opener are replaced by a SINGLE
``title_frame`` + a SINGLE ``closer_frame_takeaway`` that use the
regular theme so the deck reads as one consistent visual language.

Mirrors the loader pattern of
``backend/src/paperhub/pipelines/slide_pipeline/style_profile.py``:
yaml load via ``importlib.resources``, frozen dataclass, defensive on
missing fields, schema drift caught at load time.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Final

import yaml

__all__: Final = [
    "LayoutExample",
    "list_layout_ids",
    "load_layout_examples",
]


@dataclass(frozen=True)
class LayoutExample:
    """One layout idea in the renderer's design-reference library.

    See ``slide_layout_examples.yaml`` for the canonical contents.
    """

    id: str
    """Unique identifier — slug-cased (e.g. ``"title_frame"``)."""

    purpose: str
    """One-line summary of the design idea."""

    when_to_use: str
    """Reasoning about when this layout serves the content. The
    renderer prompt embeds this verbatim so the LLM can decide whether
    the example applies to the slide it is designing.
    """

    matches_aspect: str
    """Aspect-eligibility tag, mirroring ``slide_canvas_budget.yaml``'s
    grammar: ``">= X"`` / ``"<= X"`` / ``"X..Y"`` / ``"any"`` /
    ``"no_figure"``. Lets the agent pick a layout whose figure region
    fits the chosen figure's aspect ratio.
    """

    example: str
    """Hand-authored LaTeX example illustrating the layout. NOT a
    template the LLM must fill — just an instance of the concept
    rendered, for inspiration.
    """


# Known keys per yaml level. Anything else raises at load time so a
# typo'd field doesn't silently disappear.
_KNOWN_TOP_LEVEL_KEYS = {"version", "examples"}
_KNOWN_EXAMPLE_KEYS = {"id", "purpose", "when_to_use", "matches_aspect", "example"}


_CACHE: list[LayoutExample] | None = None


def _load_yaml_data() -> dict[str, Any]:
    path = files("paperhub.agents") / "slide_layout_examples.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            "slide_layout_examples.yaml must be a mapping with top-level "
            "key 'examples' (optionally 'version')"
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


def _parse_example(spec: Any, *, index: int) -> LayoutExample:
    if not isinstance(spec, dict):
        raise ValueError(
            f"slide_layout_examples.yaml: examples[{index}] must be a "
            f"mapping, got {type(spec).__name__}"
        )
    unknown = set(spec.keys()) - _KNOWN_EXAMPLE_KEYS
    if unknown:
        raise ValueError(
            f"slide_layout_examples.yaml: examples[{index}] has unknown "
            f"key(s) {sorted(unknown)!r}; expected only "
            f"{sorted(_KNOWN_EXAMPLE_KEYS)!r}"
        )

    def _str(key: str, *, required: bool = True) -> str:
        value = spec.get(key)
        if value is None:
            if required:
                raise ValueError(
                    f"slide_layout_examples.yaml: examples[{index}] missing "
                    f"required field {key!r}"
                )
            return ""
        # Allow YAML scalar coercion (e.g. ``no_figure`` may parse as a bare
        # word but ``0.5..1.3`` is already a string). Force-stringify to
        # tolerate either.
        if not isinstance(value, (str, int, float)):
            raise ValueError(
                f"slide_layout_examples.yaml: examples[{index}].{key} "
                f"must be a string/number, got {type(value).__name__}"
            )
        text = str(value).strip("\n").rstrip()
        if required and not text.strip():
            raise ValueError(
                f"slide_layout_examples.yaml: examples[{index}].{key} "
                "must be a non-empty string"
            )
        return text

    return LayoutExample(
        id=_str("id"),
        purpose=_str("purpose"),
        when_to_use=_str("when_to_use"),
        matches_aspect=_str("matches_aspect"),
        example=_str("example"),
    )


def load_layout_examples() -> list[LayoutExample]:
    """Load the full layout-example library in registry order.

    Caches the parsed result. Raises ``ValueError`` on schema drift
    (unknown keys, missing fields, empty fields, wrong types) so a bad
    edit fails fast instead of producing a silently-degraded library.
    """
    global _CACHE
    if _CACHE is not None:
        return list(_CACHE)
    data = _load_yaml_data()
    raw_examples = data.get("examples")
    if not isinstance(raw_examples, list) or not raw_examples:
        raise ValueError(
            "slide_layout_examples.yaml must declare a non-empty "
            "'examples' list"
        )
    parsed: list[LayoutExample] = []
    seen_ids: set[str] = set()
    for idx, spec in enumerate(raw_examples):
        entry = _parse_example(spec, index=idx)
        if entry.id in seen_ids:
            raise ValueError(
                f"slide_layout_examples.yaml: duplicate example id "
                f"{entry.id!r} at examples[{idx}]"
            )
        seen_ids.add(entry.id)
        parsed.append(entry)
    _CACHE = parsed
    return list(parsed)


def list_layout_ids() -> list[str]:
    """Return every layout id in registry order."""
    return [layout.id for layout in load_layout_examples()]
