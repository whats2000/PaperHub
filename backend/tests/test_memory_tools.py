import aiosqlite
import pytest

from paperhub.agents.memory_gate import MemoryGateRefusal
from paperhub.agents.memory_tools import (
    MemoryScopeError,
    add_memory,
    edit_memory,
    forget_memory,
    recall_memories,
)


@pytest.fixture
async def two_sessions(migrated_db: aiosqlite.Connection):
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # id 1
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # id 2
    await migrated_db.commit()
    return migrated_db


@pytest.mark.asyncio
async def test_add_and_recall_session(two_sessions) -> None:
    mid = await add_memory(two_sessions, session_id=1, content="comparing MoE routing papers", scope="session")
    assert isinstance(mid, int)
    hits = await recall_memories(two_sessions, session_id=1, query="MoE routing", scope="both")
    assert any("MoE routing" in h.content for h in hits)


@pytest.mark.asyncio
async def test_global_recall_crosses_sessions(two_sessions) -> None:
    await add_memory(two_sessions, session_id=None, content="answer in Traditional Chinese", scope="global")
    hits = await recall_memories(two_sessions, session_id=2, query="Chinese", scope="both")
    assert any("Traditional Chinese" in h.content for h in hits)


@pytest.mark.asyncio
async def test_session_recall_excludes_other_sessions(two_sessions) -> None:
    await add_memory(two_sessions, session_id=1, content="session-one secret", scope="session")
    hits = await recall_memories(two_sessions, session_id=2, query="secret", scope="both")
    assert all("session-one secret" not in h.content for h in hits)


@pytest.mark.asyncio
async def test_edit_other_session_memory_rejected(two_sessions) -> None:
    mid = await add_memory(two_sessions, session_id=1, content="owned by 1", scope="session")
    with pytest.raises(MemoryScopeError):
        await edit_memory(two_sessions, session_id=2, memory_id=mid, content="hijack")


@pytest.mark.asyncio
async def test_forget_global_allowed_from_any_session(two_sessions) -> None:
    mid = await add_memory(two_sessions, session_id=None, content="global note", scope="global")
    await forget_memory(two_sessions, session_id=2, memory_id=mid)
    hits = await recall_memories(two_sessions, session_id=2, query="global", scope="both")
    assert not hits


@pytest.mark.asyncio
async def test_add_session_without_session_id_raises(two_sessions) -> None:
    with pytest.raises(MemoryScopeError):
        await add_memory(two_sessions, session_id=None, content="x", scope="session")


@pytest.mark.asyncio
async def test_forget_other_session_memory_rejected(two_sessions) -> None:
    mid = await add_memory(two_sessions, session_id=1, content="owned by 1", scope="session")
    with pytest.raises(MemoryScopeError):
        await forget_memory(two_sessions, session_id=2, memory_id=mid)


@pytest.mark.asyncio
async def test_session_scope_recall_excludes_other_sessions(two_sessions) -> None:
    await add_memory(two_sessions, session_id=1, content="session-one secret", scope="session")
    hits = await recall_memories(two_sessions, session_id=2, query="secret", scope="session")
    assert all("session-one secret" not in h.content for h in hits)


@pytest.mark.asyncio
async def test_forget_missing_memory_raises(two_sessions) -> None:
    with pytest.raises(MemoryScopeError):
        await forget_memory(two_sessions, session_id=1, memory_id=99999)


@pytest.mark.asyncio
async def test_add_api_key_is_refused(two_sessions) -> None:
    """add_memory must raise MemoryGateRefusal for API-key content and insert nothing."""
    with pytest.raises(MemoryGateRefusal):
        await add_memory(
            two_sessions,
            session_id=1,
            content="my key is sk-abc123XYZfoo",
            scope="session",
        )
    async with two_sessions.execute("SELECT count(*) FROM memories") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 0
