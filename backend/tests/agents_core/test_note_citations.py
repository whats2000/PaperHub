"""``[note:B]`` citations are kept only for note blocks read this turn."""
from __future__ import annotations

from paperhub.agents.core.agent import _check_evidence
from paperhub.agents.core.citations import cited_notes, validate_note_citations

B1, B2 = "b_AbC123xYz9", "b_ZZZZZZZZZZ"


def test_note_citation_kept_when_read() -> None:
    text = f"Your note says so [note:{B1}]."
    out, removed = validate_note_citations(text, {B1})
    assert out == text and removed == []
    assert cited_notes(text) == [B1]


def test_note_citation_unsourced_when_not_read() -> None:
    out, removed = validate_note_citations(f"It said that [note:{B2}].", {B1})
    assert out == "It said that [unsourced:note_not_read]."
    assert removed == [B2]


def test_partial_list_keeps_read_ids() -> None:
    out, removed = validate_note_citations(f"x [note:{B1}, {B2}]", {B1})
    assert out == f"x [note:{B1}]" and removed == [B2]


def test_check_evidence_validates_notes() -> None:
    record: dict[str, object] = {}
    out = _check_evidence(f"a [note:{B2}]", set(), set(), record, {B1})
    assert "[unsourced:note_not_read]" in out
    assert record["note_citations_removed"] == [B2]


U1 = "5a070443-97b8-40ae-bbcc-ff555ee02d52"  # a block the user made in the editor


def test_editor_made_block_ids_are_note_citations() -> None:
    text = f"You doubted it [note:{U1}, {B1}]."
    assert cited_notes(text) == [U1, B1]
    out, removed = validate_note_citations(text, {U1, B1})
    assert out == text and removed == []
    out, removed = validate_note_citations(f"You doubted it [note:{U1}].", {B1})
    assert out == "You doubted it [unsourced:note_not_read]." and removed == [U1]
