import pytest

from paperhub.db.connection import open_db
from paperhub.db.deck_slides import (
    DeckSlideInput,
    get_deck_slides,
    rebuild_speaker_notes_json,
    replace_deck_slides,
    update_slide_frame,
    update_slide_note,
)
from paperhub.db.decks import get_deck, upsert_deck
from paperhub.db.migrate import apply_schema


async def _seed_deck(conn) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    await upsert_deck(
        conn, session_id=1, run_id=None, tex_path="/x/deck.tex", pdf_path=None,
        speaker_notes={}, plan={}, page_count=2, theme="metropolis",
        contributing_paper_ids=[], status="ok",
    )
    deck = await get_deck(conn, session_id=1)
    return deck.id


@pytest.mark.asyncio
async def test_replace_and_get(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        deck_id = await _seed_deck(conn)
        await replace_deck_slides(conn, deck_id=deck_id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="\\begin{frame}{A}\\end{frame}",
                           page_start=1, page_end=1),
            DeckSlideInput(slide_index=1, frame_tex="\\begin{frame}{B}\\end{frame}",
                           page_start=2, page_end=2),
        ])
        rows = await get_deck_slides(conn, deck_id=deck_id)
        assert [r.slide_index for r in rows] == [0, 1]
        assert rows[0].note_text is None and rows[0].page_end == 1


@pytest.mark.asyncio
async def test_note_update_and_rebuild_notes_json(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        deck_id = await _seed_deck(conn)
        await replace_deck_slides(conn, deck_id=deck_id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="f0", page_start=1, page_end=2),
            DeckSlideInput(slide_index=1, frame_tex="f1", page_start=3, page_end=3),
        ])
        await update_slide_note(conn, deck_id=deck_id, slide_index=0,
                                note_text="hello", note_language="English")
        notes = await rebuild_speaker_notes_json(conn, deck_id=deck_id)
        # slide 0 spans pages 1-2: page 1 gets the note, page 2 "(continued)".
        assert notes == {"1": "hello", "2": "(continued)"}
        deck = await get_deck(conn, session_id=1)
        assert deck.speaker_notes == {"1": "hello", "2": "(continued)"}


@pytest.mark.asyncio
async def test_frame_update(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        deck_id = await _seed_deck(conn)
        await replace_deck_slides(conn, deck_id=deck_id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="old", page_start=1, page_end=1),
        ])
        await update_slide_frame(conn, deck_id=deck_id, slide_index=0, frame_tex="new")
        rows = await get_deck_slides(conn, deck_id=deck_id)
        assert rows[0].frame_tex == "new"
