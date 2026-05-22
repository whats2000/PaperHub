import aiosqlite
import pytest

from paperhub.agents.memory_gate import MemoryGateRefusal
from paperhub.agents.memory_tools import (
    MemoryScopeError,
    add_memory,
    add_memory_with_supersede,
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


@pytest.mark.asyncio
async def test_supersede_marks_old_memory(two_sessions, monkeypatch) -> None:
    old_id = await add_memory(two_sessions, session_id=1, content="use Flask for the backend", scope="session")
    import paperhub.agents.memory_tools as mt_mod
    async def fake_detect(conn, new_content, scope, session_id, adapter, model):
        return old_id
    monkeypatch.setattr(mt_mod, "_detect_conflict", fake_detect)
    new_id = await add_memory_with_supersede(
        two_sessions, session_id=1, content="use FastAPI for the backend",
        scope="session", adapter=None, model="m",
    )
    async with two_sessions.execute("SELECT status, superseded_by FROM memories WHERE id = ?", (old_id,)) as cur:
        old_row = await cur.fetchone()
    async with two_sessions.execute("SELECT supersedes FROM memories WHERE id = ?", (new_id,)) as cur:
        new_row = await cur.fetchone()
    assert old_row[0] == "superseded" and old_row[1] == new_id and new_row[0] == old_id


@pytest.mark.asyncio
async def test_no_conflict_both_active(two_sessions, monkeypatch) -> None:
    import paperhub.agents.memory_tools as mt_mod
    async def no_conflict(conn, new_content, scope, session_id, adapter, model):
        return None
    monkeypatch.setattr(mt_mod, "_detect_conflict", no_conflict)
    id1 = await add_memory(two_sessions, session_id=1, content="prefer concise answers", scope="session")
    id2 = await add_memory_with_supersede(
        two_sessions, session_id=1, content="prefer numbered lists", scope="session", adapter=None, model="m",
    )
    async with two_sessions.execute("SELECT status FROM memories WHERE id IN (?, ?)", (id1, id2)) as cur:
        statuses = {r[0] for r in await cur.fetchall()}
    assert statuses == {"active"}


@pytest.mark.asyncio
async def test_superseded_memory_not_recalled(two_sessions) -> None:
    old_id = await add_memory(two_sessions, session_id=1, content="use Flask", scope="session")
    await two_sessions.execute("UPDATE memories SET status = 'superseded' WHERE id = ?", (old_id,))
    await two_sessions.commit()
    hits = await recall_memories(two_sessions, session_id=1, query="Flask", scope="both")
    assert all(h.id != old_id for h in hits), "superseded memory must not appear in recall"
