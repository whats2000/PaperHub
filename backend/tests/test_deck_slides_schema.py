import pytest

from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema


@pytest.mark.asyncio
async def test_deck_slides_table_exists(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(deck_slides)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "id", "deck_id", "slide_index", "frame_tex",
        "note_text", "note_language", "page_start", "page_end",
    }
