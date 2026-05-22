import aiosqlite
import pytest

from paperhub.db.decks import DeckRow, get_deck, upsert_deck  # noqa: F401


@pytest.mark.asyncio
async def test_upsert_and_get(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    await upsert_deck(
        migrated_db, session_id=1, run_id=1, tex_path="slides/deck.tex",
        pdf_path="slides/deck.pdf", speaker_notes={"1": "hi"},
        plan={"title": "T", "sections": []}, page_count=3, theme="metropolis",
        contributing_paper_ids=[1, 2], status="ok",
    )
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    assert deck.page_count == 3
    assert deck.speaker_notes == {"1": "hi"}
    assert deck.contributing_paper_ids == [1, 2]
    await upsert_deck(
        migrated_db, session_id=1, run_id=1, tex_path="slides/deck.tex",
        pdf_path="slides/deck.pdf", speaker_notes={}, plan={}, page_count=5,
        theme="metropolis", contributing_paper_ids=[1], status="ok",
    )
    deck2 = await get_deck(migrated_db, session_id=1)
    assert deck2 is not None and deck2.page_count == 5
