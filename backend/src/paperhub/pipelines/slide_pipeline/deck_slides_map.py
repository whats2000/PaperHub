"""Map a FINAL compiled Beamer deck to one DeckSlideInput per frame, with the
PDF page span each frame occupies (Plan F4 — SRS v2.21).

Frames and page groups are both walked in document order, so they zip 1:1. A
leading \\maketitle page has no \\begin{frame} block, so when there is one more
page group than frame, the groups are tail-anchored to the frames.

``extract_frames_from_beamer`` also emits a synthetic ``r"\\maketitle"`` tuple
when \\maketitle appears immediately before the first frame; that tuple is
filtered out here (it corresponds to a title page, not a content frame).
"""
from __future__ import annotations

from paperhub.db.deck_slides import DeckSlideInput
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
)
from paperhub.pipelines.slide_pipeline.frame_map import (
    group_logical_slides,
    map_pages_to_slides,
)

# The synthetic entry emitted by extract_frames_from_beamer for a bare \maketitle
# before the first frame has this exact frame_content value.
_SYNTHETIC_MAKETITLE = r"\maketitle"


def build_deck_slides(final_tex: str, page_count: int) -> list[DeckSlideInput]:
    """Return one :class:`DeckSlideInput` per real Beamer frame, each annotated
    with the PDF page span it occupies.

    Parameters
    ----------
    final_tex:
        The final compiled Beamer LaTeX source (post Overfull-fix loop).
    page_count:
        Number of pages in the compiled PDF (used only by the fallback path).
        On an unexpected frame/page-count mismatch, falls back to one sequential
        page per frame (page_count=0 is treated as 1).
    """
    # Drop synthetic \maketitle tuples — they are title-page markers, not
    # real content frames, and they would throw off the 1:1 zip with groups.
    raw_frames = extract_frames_from_beamer(final_tex)
    frames = [
        (num, content, s, e)
        for num, content, s, e in raw_frames
        if content.strip() != _SYNTHETIC_MAKETITLE
    ]

    groups = group_logical_slides(map_pages_to_slides(final_tex))  # [[page,...]]

    if not frames:
        return []

    # Align page groups to frames. Both are in document order, so they zip
    # 1:1 — EXCEPT a bare \maketitle title page adds exactly one extra leading
    # group with no frame block (extract_frames synthesises a "\maketitle"
    # tuple only when \maketitle precedes \begin{frame}; we filter those above,
    # so that page reappears here as one unmatched leading group). Absorb that
    # single offset; route any other count mismatch to the fallback.
    if len(groups) == len(frames):
        aligned: list[list[int]] | None = groups
    elif len(groups) == len(frames) + 1:
        aligned = groups[1:]
    else:
        aligned = None

    if aligned is not None:
        return [
            DeckSlideInput(
                slide_index=idx,
                frame_tex=content,
                page_start=min(grp),
                page_end=max(grp),
            )
            for idx, ((_num, content, _s, _e), grp) in enumerate(zip(frames, aligned, strict=True))
        ]

    # Fallback: page-count mismatch (unexpected — \pause is forbidden in drafts).
    # Assign each frame one sequential page; clamp to page_count.
    rows: list[DeckSlideInput] = []
    for idx, (_num, content, _s, _e) in enumerate(frames):
        page = min(idx + 1, max(page_count, 1))
        rows.append(
            DeckSlideInput(
                slide_index=idx, frame_tex=content, page_start=page, page_end=page
            )
        )
    return rows


__all__ = ["build_deck_slides"]
