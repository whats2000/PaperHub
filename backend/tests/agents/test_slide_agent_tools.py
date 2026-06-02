import pytest

from paperhub.agents.slide_agent_tools import (
    DeckState,
    apply_delete_frame,
    apply_initial_draft,
    apply_insert_frame_after,
    apply_replace_frame,
    apply_replace_preamble,
)

_DECK = r"""\documentclass{beamer}
\usetheme{Berlin}
\begin{document}
\begin{frame}{A}body of A\end{frame}
\begin{frame}{B}body of B\end{frame}
\begin{frame}{C}body of C\end{frame}
\end{document}
"""


def test_initial_draft_sets_deck_tex():
    state = DeckState(deck_tex="", preamble="\\documentclass{beamer}", workdir=None)
    state2 = apply_initial_draft(state, deck_tex=_DECK)
    assert state2.deck_tex == _DECK


def test_apply_initial_draft_rejects_missing_documentclass():
    state = DeckState(deck_tex="", preamble="", workdir=None)
    bad_tex = r"\title{X}\begin{document}\begin{frame}{x}y\end{frame}\end{document}"
    with pytest.raises(ValueError, match=r"\\documentclass"):
        apply_initial_draft(state, deck_tex=bad_tex)


def test_apply_initial_draft_rejects_missing_begin_document():
    state = DeckState(deck_tex="", preamble="", workdir=None)
    bad_tex = r"\documentclass{beamer}\begin{frame}{x}y\end{frame}\end{document}"
    with pytest.raises(ValueError, match=r"\\begin\{document\}"):
        apply_initial_draft(state, deck_tex=bad_tex)


def test_apply_initial_draft_rejects_missing_end_document():
    state = DeckState(deck_tex="", preamble="", workdir=None)
    bad_tex = r"\documentclass{beamer}\begin{document}\begin{frame}{x}y\end{frame}"
    with pytest.raises(ValueError, match=r"\\end\{document\}"):
        apply_initial_draft(state, deck_tex=bad_tex)


def test_apply_initial_draft_accepts_valid_minimal_deck():
    state = DeckState(deck_tex="", preamble="", workdir=None)
    good_tex = (
        r"\documentclass{beamer}\begin{document}"
        r"\begin{frame}{x}y\end{frame}\end{document}"
    )
    s2 = apply_initial_draft(state, deck_tex=good_tex)
    assert s2.deck_tex == good_tex


def test_replace_frame_swaps_one_frame_by_index():
    state = DeckState(deck_tex=_DECK, preamble="", workdir=None)
    state2 = apply_replace_frame(
        state, frame_index=1, new_frame_tex=r"\begin{frame}{B2}rewritten B\end{frame}"
    )
    assert "rewritten B" in state2.deck_tex
    assert "body of B" not in state2.deck_tex
    assert "body of A" in state2.deck_tex   # A untouched
    assert "body of C" in state2.deck_tex   # C untouched


def test_replace_frame_rejects_out_of_range():
    state = DeckState(deck_tex=_DECK, preamble="", workdir=None)
    with pytest.raises(IndexError):
        apply_replace_frame(state, frame_index=99, new_frame_tex=r"\begin{frame}{x}y\end{frame}")


def test_replace_frame_rejects_non_frame_content():
    state = DeckState(deck_tex=_DECK, preamble="", workdir=None)
    with pytest.raises(ValueError, match="must be a single .* frame env"):
        apply_replace_frame(state, frame_index=0, new_frame_tex="just some text")


def test_insert_frame_after_grows_the_deck():
    state = DeckState(deck_tex=_DECK, preamble="", workdir=None)
    state2 = apply_insert_frame_after(
        state, frame_index=0, new_frame_tex=r"\begin{frame}{A2}inserted\end{frame}"
    )
    # A2 must appear AFTER A and BEFORE B.
    a_pos = state2.deck_tex.index("body of A")
    a2_pos = state2.deck_tex.index("inserted")
    b_pos = state2.deck_tex.index("body of B")
    assert a_pos < a2_pos < b_pos


def test_delete_frame_removes_one_frame():
    state = DeckState(deck_tex=_DECK, preamble="", workdir=None)
    state2 = apply_delete_frame(state, frame_index=1)
    assert "body of B" not in state2.deck_tex
    assert "body of A" in state2.deck_tex
    assert "body of C" in state2.deck_tex


def test_replace_preamble_replaces_only_the_preamble_block():
    state = DeckState(deck_tex=_DECK, preamble="", workdir=None)
    new_preamble = r"\documentclass{beamer}\usetheme{Madrid}"
    state2 = apply_replace_preamble(state, new_preamble=new_preamble)
    assert "\\usetheme{Madrid}" in state2.deck_tex
    assert "Berlin" not in state2.deck_tex
    # Body untouched.
    assert "body of A" in state2.deck_tex
    assert state2.preamble == new_preamble


def test_replace_preamble_rejects_text_with_begin_document():
    state = DeckState(deck_tex=_DECK, preamble="", workdir=None)
    with pytest.raises(ValueError, match="must not contain"):
        apply_replace_preamble(
            state, new_preamble=r"\documentclass{beamer}\begin{document}"
        )
