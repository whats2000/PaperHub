import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_decks_table_exists_with_unique_session(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await migrated_db.execute(
        "INSERT INTO decks (session_id, tex_path, page_count, theme, contributing_paper_ids_json) "
        "VALUES (1, 'slides/deck.tex', 0, 'metropolis', '[]')"
    )
    await migrated_db.commit()
    # SQLite raises IntegrityError on execute (constraint checked immediately),
    # so the violating execute is the assertion target — no commit needed inside.
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO decks (session_id, tex_path, page_count, theme, contributing_paper_ids_json) "
            "VALUES (1, 'slides/other.tex', 0, 'metropolis', '[]')"
        )


@pytest.mark.asyncio
async def test_decks_status_check(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO decks (session_id, tex_path, page_count, theme, contributing_paper_ids_json, status) "
            "VALUES (1, 'x', 0, 'metropolis', '[]', 'bogus')"
        )
