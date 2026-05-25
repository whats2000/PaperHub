# Adapted from reference/paper2slides-plus/src/beamer_utils.py @ 88515c4
# (extract_frames_from_beamer / _count_frame_pages page-counting logic).
# Original project: https://github.com/whats2000/paper2slides-plus (MIT).
"""Map final-PDF pages back to logical Beamer slides for layout-aware notes.

The F3 Overfull-aware compile loop may SPLIT one overflowing logical slide
into K consecutive frames that share the same ``\\frametitle`` (each rendering
as one PDF page), and the metropolis theme prepends a ``\\titlepage`` page.
This module reconstructs, in document order, which PDF page maps to which
logical slide so a later task can split the speaker note per page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from paperhub.pipelines.slide_pipeline.beamer_helpers import _count_frame_pages


@dataclass(frozen=True)
class PageSlide:
    """One PDF page and the logical slide (frame) it renders."""

    page: int  # 1-based PDF page
    frametitle: str | None
    is_title: bool  # True for a \titlepage/\maketitle/structural page (no logical slide)


# A \maketitle that sits before the first frame renders as its own page 1.
_MAKETITLE_RE = re.compile(r"\\maketitle")
_FIRST_FRAME_RE = re.compile(r"\\begin\{frame\}")
# Whole frame environment (non-greedy, dotall) — same anchor the reference uses.
_FRAME_RE = re.compile(r"\\begin\{frame\}.*?\\end\{frame\}", re.DOTALL)
# Short title form: \begin{frame}{Title} — possibly preceded by an overlay spec
# and/or options, e.g. \begin{frame}<2->[fragile]{Title}.
_FRAME_SHORT_TITLE_RE = re.compile(
    r"\\begin\{frame\}\s*(?:<[^>]*>)?\s*(?:\[[^\]]*\])?\s*\{(.*?)\}",
    re.DOTALL,
)
# Command form: \frametitle{Title} anywhere inside the frame body.
_FRAMETITLE_CMD_RE = re.compile(r"\\frametitle\s*(?:<[^>]*>)?\s*\{(.*?)\}", re.DOTALL)
# A \titlepage inside a frame makes that frame the structural title page.
_TITLEPAGE_RE = re.compile(r"\\titlepage")


def _extract_frametitle(frame_content: str) -> str | None:
    """Pull the frame title from either the ``\\frametitle{...}`` command form
    or the ``\\begin{frame}{...}`` short form. Returns ``None`` if absent."""
    cmd = _FRAMETITLE_CMD_RE.search(frame_content)
    if cmd is not None:
        return cmd.group(1).strip()
    short = _FRAME_SHORT_TITLE_RE.search(frame_content)
    if short is not None:
        title = short.group(1).strip()
        # The short form's brace group is optional; an empty {} means no title.
        return title if title else None
    return None


def map_pages_to_slides(final_tex: str) -> list[PageSlide]:
    """Walk the compiled Beamer source and return one :class:`PageSlide` per
    rendered PDF page, in document order with sequential 1-based page numbers.

    - A ``\\maketitle`` before the first frame → a title page.
    - A frame containing ``\\titlepage`` → a title page.
    - Every other ``\\begin{frame}...\\end{frame}`` → ``_count_frame_pages``
      entries (normally 1; >1 only if a stray overlay spec is present), all
      sharing the frame's extracted title.
    """
    pages: list[PageSlide] = []
    page = 0

    # \maketitle before the first frame renders as page 1 (mirror the reference).
    maketitle_match = _MAKETITLE_RE.search(final_tex)
    first_frame_match = _FIRST_FRAME_RE.search(final_tex)
    if maketitle_match is not None and (
        first_frame_match is None
        or maketitle_match.start() < first_frame_match.start()
    ):
        page += 1
        pages.append(PageSlide(page=page, frametitle=None, is_title=True))

    for match in _FRAME_RE.finditer(final_tex):
        frame_content = match.group(0)
        if _TITLEPAGE_RE.search(frame_content) is not None:
            page += 1
            pages.append(PageSlide(page=page, frametitle=None, is_title=True))
            continue

        frametitle = _extract_frametitle(frame_content)
        for _ in range(_count_frame_pages(frame_content)):
            page += 1
            pages.append(
                PageSlide(page=page, frametitle=frametitle, is_title=False)
            )

    return pages


def _normalize_title(title: str | None) -> str | None:
    """Whitespace-collapse a frametitle for grouping. ``None`` stays ``None``."""
    if title is None:
        return None
    return re.sub(r"\s+", " ", title).strip()


def group_logical_slides(pages: list[PageSlide]) -> list[list[int]]:
    """Collapse CONSECUTIVE content pages sharing the same normalized frametitle
    into one logical-slide group, in document order.

    - Title pages are each their own single-page group (still returned so callers
      can flag them).
    - A page with a ``None`` title never coalesces — it groups alone.

    e.g. title + frameA + frameA(split) + frameB → ``[[1], [2, 3], [4]]``.
    """
    groups: list[list[int]] = []
    prev_key: str | None = None
    prev_was_groupable = False

    for ps in pages:
        if ps.is_title:
            groups.append([ps.page])
            prev_was_groupable = False
            prev_key = None
            continue

        key = _normalize_title(ps.frametitle)
        # Only coalesce when the previous page was a groupable (non-title,
        # non-None) page with the same normalized title.
        if (
            prev_was_groupable
            and key is not None
            and key == prev_key
            and groups
        ):
            groups[-1].append(ps.page)
        else:
            groups.append([ps.page])

        prev_was_groupable = key is not None
        prev_key = key

    return groups
