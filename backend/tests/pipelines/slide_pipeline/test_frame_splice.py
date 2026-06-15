"""Tests for splice_frame — the manual single-frame editor's deck-tex rewrite.

A manual "edit current frame" save replaces exactly ONE frame body in the full
deck source with the user's edited frame, then recompiles the whole deck. The
stored ``deck_slides.frame_tex`` is byte-identical to the frame body in
``deck.tex`` (both come from ``extract_frames_from_beamer``), so the splice
locates the old frame by exact substring.
"""
import pytest

from paperhub.agents.sl_cite import serialize_cite, strip_cite
from paperhub.pipelines.slide_pipeline.frame_splice import (
    set_frame_cite_marker,
    splice_frame,
)

_DECK = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{Title A}
First frame body.
\end{frame}

% cite: 7:Introduction
\begin{frame}{Title B}
Second frame body.
\end{frame}
\end{document}
"""

_OLD_B = "\\begin{frame}{Title B}\nSecond frame body.\n\\end{frame}"
_NEW_B = "\\begin{frame}{Title B}\nEdited second frame.\n\\end{frame}"
_OLD_B_INBODY = (
    "\\begin{frame}{Title B}\n% cite: 7:Introduction\nSecond frame body.\n\\end{frame}"
)


def test_splice_replaces_the_matching_frame() -> None:
    out = splice_frame(_DECK, _OLD_B, _NEW_B)
    assert _NEW_B in out
    assert _OLD_B not in out
    # The other frame + the preceding % cite: marker survive verbatim.
    assert "\\begin{frame}{Title A}\nFirst frame body.\n\\end{frame}" in out
    assert "% cite: 7:Introduction" in out


def test_splice_replaces_exactly_one_occurrence() -> None:
    out = splice_frame(_DECK, _OLD_B, _NEW_B)
    assert out.count(_NEW_B) == 1


def test_splice_raises_when_old_frame_absent() -> None:
    with pytest.raises(ValueError, match="not found"):
        splice_frame(_DECK, "\\begin{frame}{Nope}\nx\n\\end{frame}", _NEW_B)


def test_splice_raises_when_old_frame_ambiguous() -> None:
    dup = _DECK + "\n" + _OLD_B  # the same frame body now appears twice
    with pytest.raises(ValueError, match="ambiguous"):
        splice_frame(dup, _OLD_B, _NEW_B)


# ── drop_preceding_cite: a manual frame edit must not inherit the OLD,
#    out-of-body % cite: marker (it grounded the previous content). ──────────

_FRAME_A = "\\begin{frame}{Title A}\nFirst frame body.\n\\end{frame}"


def test_splice_drops_the_preceding_cite_when_requested() -> None:
    out = splice_frame(_DECK, _OLD_B, _NEW_B, drop_preceding_cite=True)
    assert _NEW_B in out
    # The stale auto-marker that sat just before the edited frame is removed,
    # so grounding re-resolves from the user's new frame (unsourced unless they
    # added their own in-body % cite:).
    assert "% cite: 7:Introduction" not in out


def test_splice_keeps_the_preceding_cite_by_default() -> None:
    out = splice_frame(_DECK, _OLD_B, _NEW_B)
    assert "% cite: 7:Introduction" in out


def test_splice_drop_strips_only_the_edited_frames_marker() -> None:
    deck = (
        "\\begin{document}\n"
        "% cite: 1:Background\n" + _FRAME_A + "\n"  # another frame's marker
        "% cite: 7:Introduction\n" + _OLD_B + "\n"
        "\\end{document}\n"
    )
    out = splice_frame(deck, _OLD_B, _NEW_B, drop_preceding_cite=True)
    assert "% cite: 1:Background" in out  # untouched — belongs to frame A
    assert "% cite: 7:Introduction" not in out  # the edited frame's stale marker


def test_splice_drop_honors_an_in_body_marker_the_user_added() -> None:
    # The user's new frame carries its OWN cite — it lives inside the frame body
    # so it survives (only the preceding out-of-body marker is dropped).
    new = "\\begin{frame}{Title B}\n% cite: 9:Method\nUser-written body.\n\\end{frame}"
    out = splice_frame(_DECK, _OLD_B, new, drop_preceding_cite=True)
    assert "% cite: 9:Method" in out
    assert "% cite: 7:Introduction" not in out


# ── strip_cite / serialize_cite / set_frame_cite_marker (structured cites) ──


def test_strip_cite_removes_in_body_marker() -> None:
    out = strip_cite(_OLD_B_INBODY)
    assert "% cite:" not in out
    assert out.startswith("\\begin{frame}{Title B}")
    assert "Second frame body." in out


def test_strip_cite_noop_without_marker() -> None:
    assert strip_cite(_OLD_B) == _OLD_B


def test_serialize_cite_roundtrips() -> None:
    assert serialize_cite([(47, "Introduction")]) == "% cite: 47:Introduction"
    assert serialize_cite([(47, "Intro"), (53, "Method")]) == (
        "% cite: 47:Intro; 53:Method"
    )
    assert serialize_cite([]) == ""  # unsourced → no marker


def test_set_frame_cite_marker_moves_in_body_to_preceding() -> None:
    deck = "\\begin{document}\n" + _OLD_B_INBODY + "\n\\end{document}\n"
    new_deck, new_body = set_frame_cite_marker(deck, _OLD_B_INBODY, "% cite: 9:Method")
    assert "% cite: 9:Method\n\\begin{frame}{Title B}" in new_deck
    assert "% cite:" not in new_body
    assert new_body.startswith("\\begin{frame}{Title B}")
    assert new_deck.count("% cite:") == 1  # no duplicate


def test_set_frame_cite_marker_empty_removes_it() -> None:
    deck = "\\begin{document}\n% cite: 7:Introduction\n" + _OLD_B + "\n\\end{document}\n"
    new_deck, new_body = set_frame_cite_marker(deck, _OLD_B, "")
    assert "% cite:" not in new_deck  # removed → unsourced
    assert new_body == _OLD_B
