"""Regression test for an F4 fix surfaced by the real-API gate:

`_select_rows` page-scope with no explicit target_page falls back to the
on-screen page (the classifier returned target_scope="page"/target_page=None
for the Chinese ordinal "第三頁").

(The companion `parse_slide_budget` regex test was removed with that function:
deck length is no longer parsed by regex — the outline reads the user's
requested length directly from the task, any language.)
"""
from paperhub.agents.report_graph import _select_rows
from paperhub.db.deck_slides import DeckSlideRow
from paperhub.models.domain import DeckCommand


def _row(idx: int, ps: int, pe: int) -> DeckSlideRow:
    return DeckSlideRow(
        id=idx, deck_id=1, slide_index=idx, frame_tex="f",
        note_text=None, note_language=None, page_start=ps, page_end=pe,
    )


def test_select_rows_page_without_number_falls_back_to_current_view() -> None:
    rows = [_row(0, 1, 1), _row(1, 2, 2), _row(2, 3, 3)]
    cmd = DeckCommand(action="edit_slides", target_scope="page", target_page=None, note_language=None)
    sel = _select_rows(rows, cmd, current_view_page=3)
    assert [r.slide_index for r in sel] == [2]


def test_select_rows_page_with_explicit_number() -> None:
    rows = [_row(0, 1, 1), _row(1, 2, 2)]
    cmd = DeckCommand(action="edit_slides", target_scope="page", target_page=2, note_language=None)
    sel = _select_rows(rows, cmd, current_view_page=1)
    assert [r.slide_index for r in sel] == [1]


def test_select_rows_page_unresolvable_returns_empty() -> None:
    # target_page None AND current_view_page out of range → genuinely empty.
    rows = [_row(0, 1, 1)]
    cmd = DeckCommand(action="edit_slides", target_scope="page", target_page=None, note_language=None)
    assert _select_rows(rows, cmd, current_view_page=9) == []
