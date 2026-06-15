"""Frame-tex manipulation for the manual / structured slide editors.

The Slides panel's per-frame editor sends back a single edited
``\\begin{frame}…\\end{frame}`` body; :func:`splice_frame` swaps it into
``deck.tex`` in place of the original frame, leaving every other byte untouched.
:func:`set_frame_cite_marker` rewrites a frame's grounding ``% cite:`` comment
deterministically (the structured Sources editor). Both are pure string utils —
no dependency on the agents layer.

The stored ``deck_slides.frame_tex`` is byte-identical to the frame body in
``deck.tex`` (both produced by ``extract_frames_from_beamer``), so the match is
an exact substring. A frame that appears zero or more-than-once is surfaced as a
``ValueError`` rather than silently mishandled — the caller (the manual-edit
endpoint) returns the error and the user falls back to "Edit all deck".
"""
from __future__ import annotations

import re

# A full ``% cite:`` comment LINE (incl. its trailing newline), for stripping
# the marker out of a frame so the content editor shows slide content only.
_CITE_LINE_RE = re.compile(
    r"^[ \t]*%[ \t]*cite:[^\n]*\n?", re.MULTILINE | re.IGNORECASE
)

# A ``% cite:`` comment line sitting IMMEDIATELY before a frame (the agent often
# places the grounding marker there, where frame extraction strips it out of
# ``frame_tex``). Anchored to the end of the pre-frame text so only the marker
# that belongs to the edited frame is matched, not an earlier frame's marker.
_PRECEDING_CITE_RE = re.compile(
    r"[ \t]*%[ \t]*cite:[^\n]*\r?\n(?=[ \t]*\Z)", re.IGNORECASE
)


def strip_cite(frame_tex: str) -> str:
    """Return ``frame_tex`` with every ``% cite:`` comment line removed — the
    content editor shows slide CONTENT only; grounding is managed structurally
    (the Sources reference editor), never by hand-editing the comment."""
    return _CITE_LINE_RE.sub("", frame_tex)


def splice_frame(deck_tex: str, old_frame_tex: str, new_frame_tex: str) -> str:
    """Return ``deck_tex`` with the single ``old_frame_tex`` block replaced by
    ``new_frame_tex``.

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
    return deck_tex.replace(old_frame_tex, new_frame_tex, 1)


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


__all__ = ["splice_frame", "set_frame_cite_marker", "strip_cite"]
