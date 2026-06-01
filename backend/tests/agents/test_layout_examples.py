"""F4.5 layout-examples loader tests.

Asserts the F4.5 yaml shape (aspect-tagged, title+closer as normal frames)
and the loader contract. Replaces the F4.4 R1 assertions which assumed a
separate ``closer_*`` family with ``[plain]`` + ``\\Large`` decorations —
those entries are deliberately removed by F4.5.
"""
from __future__ import annotations

import paperhub.agents._layout_examples as _layout_examples
from paperhub.agents._layout_examples import (
    LayoutExample,
    list_layout_ids,
    load_layout_examples,
)


def _reset_cache() -> None:
    """Force a fresh yaml read so per-test edits (if any) are reflected."""
    _layout_examples._CACHE = None


def test_layout_ids_contain_canonical_set() -> None:
    _reset_cache()
    ids = set(list_layout_ids())
    required = {
        "title_frame",
        "closer_frame_takeaway",
        "executive_summary",
        "figure_left_half_portrait_with_bullets",
        "figure_top_full_landscape_with_caption",
        "equation_centered_with_notation",
        "comparison_table",
        "text_only_motivation",
        "split_overfull_into_two",
    }
    assert required.issubset(ids), (
        f"missing canonical F4.5 ids: {sorted(required - ids)!r}"
    )


def test_no_separate_title_or_closer_family_with_plain_or_large_decoration() -> None:
    _reset_cache()
    examples = load_layout_examples()
    for ex in examples:
        if ex.id in ("title_frame", "closer_frame_takeaway"):
            # F4.5 contract: title + closer use normal \begin{frame}{Title},
            # NOT [plain] + Large.
            assert "[plain]" not in ex.example, (
                f"{ex.id} must NOT use [plain] (F4.5 style consistency)"
            )
            # \Large / \rule decorations are the R1 antipattern — banned.
            assert "\\Large" not in ex.example, (
                f"{ex.id} must NOT use \\Large centered (use theme)"
            )


def test_matches_aspect_is_present_on_every_example() -> None:
    _reset_cache()
    for ex in load_layout_examples():
        assert isinstance(ex, LayoutExample)
        assert ex.matches_aspect, f"{ex.id} missing matches_aspect"


def test_each_example_compiles_at_least_to_a_well_formed_frame_block() -> None:
    _reset_cache()
    for ex in load_layout_examples():
        # Structural sanity — every example block that opens a frame env
        # must also close one.
        if "\\begin{frame}" in ex.example:
            assert "\\end{frame}" in ex.example, (
                f"{ex.id} example has unclosed frame"
            )
