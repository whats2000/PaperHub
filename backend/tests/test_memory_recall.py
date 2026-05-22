import aiosqlite
import pytest

from paperhub.agents.memory_recall import build_memory_context_block
from paperhub.agents.memory_tools import add_memory


@pytest.mark.asyncio
async def test_context_block_includes_relevant_memories(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await add_memory(migrated_db, session_id=None, content="answer in Traditional Chinese", scope="global")
    block = await build_memory_context_block(migrated_db, session_id=1, query="Chinese answer please", enabled=True)
    assert "Traditional Chinese" in block
    assert block.startswith("Relevant remembered facts")


@pytest.mark.asyncio
async def test_disabled_returns_empty(migrated_db: aiosqlite.Connection) -> None:
    block = await build_memory_context_block(migrated_db, session_id=1, query="x", enabled=False)
    assert block == ""


@pytest.mark.asyncio
async def test_no_hits_returns_empty(migrated_db: aiosqlite.Connection) -> None:
    block = await build_memory_context_block(migrated_db, session_id=1, query="nothingmatches", enabled=True)
    assert block == ""


@pytest.mark.asyncio
async def test_context_block_format(migrated_db: aiosqlite.Connection) -> None:
    """Block contains the scope label and content for each hit."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await add_memory(migrated_db, session_id=None, content="prefer bullet lists", scope="global")
    block = await build_memory_context_block(
        migrated_db, session_id=1, query="prefer bullet lists", enabled=True,
    )
    assert "(global)" in block
    assert "prefer bullet lists" in block


@pytest.mark.asyncio
async def test_session_memory_appears_in_own_session(migrated_db: aiosqlite.Connection) -> None:
    """Session-scoped memories are visible from the same session."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await add_memory(migrated_db, session_id=1, content="use LaTeX formatting", scope="session")
    block = await build_memory_context_block(
        migrated_db, session_id=1, query="LaTeX formatting", enabled=True,
    )
    assert "LaTeX formatting" in block


@pytest.mark.asyncio
async def test_session_memory_hidden_from_other_sessions(migrated_db: aiosqlite.Connection) -> None:
    """Session-scoped memories are NOT visible from a different session."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # session 1
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # session 2
    await migrated_db.commit()
    await add_memory(migrated_db, session_id=1, content="session one secret fact", scope="session")
    block = await build_memory_context_block(
        migrated_db, session_id=2, query="secret fact", enabled=True,
    )
    assert block == ""


@pytest.mark.asyncio
async def test_superseded_fact_not_in_context_block(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await add_memory(migrated_db, session_id=None, content="answer in English", scope="global")
    await migrated_db.execute("UPDATE memories SET status = 'superseded'")
    await migrated_db.commit()
    block = await build_memory_context_block(migrated_db, session_id=1, query="English language", enabled=True)
    assert "answer in English" not in block
