"""Replace one frame body in a full deck source (manual "edit current frame").

The Slides panel's per-frame editor sends back a single edited
``\\begin{frame}…\\end{frame}`` block; this splices it into ``deck.tex`` in
place of the original frame, leaving every other byte — including a preceding
``% cite:`` grounding marker, which sits OUTSIDE the frame body — untouched.

The stored ``deck_slides.frame_tex`` is byte-identical to the frame body in
``deck.tex`` (both produced by ``extract_frames_from_beamer``), so the match is
an exact substring. A frame that appears zero or more-than-once is surfaced as a
``ValueError`` rather than silently mishandled — the caller (the manual-edit
endpoint) returns the error and the user falls back to "Edit all deck".
"""
from __future__ import annotations

import re

from paperhub.agents.sl_cite import strip_cite

# A ``% cite:`` comment line sitting IMMEDIATELY before a frame (the agent often
# places the grounding marker there, where frame extraction strips it out of
# ``frame_tex``). Anchored to the end of the pre-frame text so only the marker
# that belongs to the edited frame is matched, not an earlier frame's marker.
_PRECEDING_CITE_RE = re.compile(
    r"[ \t]*%[ \t]*cite:[^\n]*\r?\n(?=[ \t]*\Z)", re.IGNORECASE
)


def splice_frame(
    deck_tex: str,
    old_frame_tex: str,
    new_frame_tex: str,
    *,
    drop_preceding_cite: bool = False,
) -> str:
    """Return ``deck_tex`` with the single ``old_frame_tex`` block replaced by
    ``new_frame_tex``.

    ``drop_preceding_cite`` (manual frame edit): also strip a ``% cite:`` marker
    that sits just BEFORE the frame (outside ``frame_tex``, so invisible in the
    per-frame editor). That marker grounded the PREVIOUS content; carrying it
    onto hand-rewritten content would silently mislabel the source. Dropping it
    makes grounding re-resolve from the user's new frame — an in-body ``% cite:``
    they wrote is honored; otherwise the slide is correctly unsourced. (A
    whole-deck edit shows every marker in the raw tex, so it needs no stripping.)

    Raises
    ------
    ValueError
        If ``old_frame_tex`` does not appear in ``deck_tex`` ("not found"), or
        appears more than once ("ambiguous" — two byte-identical frames, which
        the splice refuses to guess between).
    """
    count = deck_tex.count(old_frame_tex)
    if count == 0:
        raise ValueError("frame not found in deck source")
    if count > 1:
        raise ValueError(
            f"frame is ambiguous (matches {count} locations in the deck source)"
        )
    idx = deck_tex.index(old_frame_tex)
    prefix, suffix = deck_tex[:idx], deck_tex[idx + len(old_frame_tex):]
    if drop_preceding_cite:
        prefix = _PRECEDING_CITE_RE.sub("", prefix)
    return prefix + new_frame_tex + suffix


def set_frame_cite_marker(
    deck_tex: str, old_frame_tex: str, marker_line: str
) -> tuple[str, str]:
    """Set a frame's ``% cite:`` marker to ``marker_line`` (or remove it when
    empty), normalizing it to a single PRECEDING comment and leaving the frame
    body content-only.

    Used by the structured Sources reference editor: it rewrites the grounding
    comment deterministically (the user never hand-edits it). Returns
    ``(new_deck_tex, new_frame_body)`` — the new body (cite stripped) is what
    ``deck_slides.frame_tex`` becomes, so the DB and the deck source stay in
    sync. A pure-comment change: the compiled PDF is unaffected, so no recompile.

    Raises ``ValueError`` if the frame is absent / ambiguous (as ``splice_frame``).
    """
    count = deck_tex.count(old_frame_tex)
    if count == 0:
        raise ValueError("frame not found in deck source")
    if count > 1:
        raise ValueError(
            f"frame is ambiguous (matches {count} locations in the deck source)"
        )
    new_body = strip_cite(old_frame_tex)
    idx = deck_tex.index(old_frame_tex)
    prefix, suffix = deck_tex[:idx], deck_tex[idx + len(old_frame_tex):]
    prefix = _PRECEDING_CITE_RE.sub("", prefix)  # drop any old preceding marker
    block = f"{marker_line}\n{new_body}" if marker_line else new_body
    return prefix + block + suffix, new_body


__all__ = ["splice_frame", "set_frame_cite_marker"]
