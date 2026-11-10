"""Plan H2 fix C1: one shared parser for the app's chunk-citation markers."""
from __future__ import annotations

from paperhub.feedback.citations import cited_chunk_ids


def test_single_marker() -> None:
    assert cited_chunk_ids("A [chunk:7] (p.1).") == [7]


def test_multi_id_marker() -> None:
    assert cited_chunk_ids("A [chunk:101,102].") == [101, 102]


def test_spaced_multi_id_marker() -> None:
    assert cited_chunk_ids("A [chunk: 101, 102 ].") == [101, 102]


def test_repeated_chunk_prefix_marker() -> None:
    assert cited_chunk_ids("A [chunk:1, chunk:2].") == [1, 2]


def test_order_preserving_dedupe_across_markers() -> None:
    text = "x [chunk:5] y [chunk:3,5] z [chunk: 9, chunk:3]"
    assert cited_chunk_ids(text) == [5, 3, 9]


def test_no_marker() -> None:
    assert cited_chunk_ids("no citations, chunk 4 [p.2]") == []


def test_strip_unsourced_marks() -> None:
    """System-added [unsourced:…] marks are for the reader, not training
    targets; the export strips them like the removed citations they replace."""
    from paperhub.feedback.citations import strip_unsourced

    assert strip_unsourced("Adam [unsourced:not_read]. Loss L [chunk:3].") == (
        "Adam. Loss L [chunk:3].")
    assert strip_unsourced('"quote" [unsourced: quote_not_found], then') == '"quote", then'


async def test_export_answer_target_has_no_unsourced_marks(migrated_db) -> None:  # type: ignore[no-untyped-def]
    from paperhub.feedback.export import _answer

    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.executemany(
        "INSERT INTO messages (session_id, role, content, run_id) VALUES (1, ?, ?, 1)",
        [("user", "Which optimiser?"),
         ("assistant", "AdamW [unsourced:not_read], lr 1e-4 [chunk:7].")])
    await migrated_db.commit()
    a = await _answer(migrated_db, 1)
    assert a is not None and a.answer == "AdamW, lr 1e-4 [chunk:7]."
