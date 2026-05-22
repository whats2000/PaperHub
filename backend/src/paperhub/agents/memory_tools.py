"""Memory dispatchers (SRS v2.16 FR-10). Pure DB ops over the `memories`
table; scope/ownership enforced deterministically (NFR-05)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import aiosqlite

from paperhub.agents.memory_gate import MemoryGateRefusal, classify_memory_safety

if TYPE_CHECKING:
    from paperhub.llm.adapter import LlmAdapter

Scope = Literal["session", "global"]
RecallScope = Literal["session", "global", "both"]


class MemoryScopeError(Exception):
    """Raised when an edit/forget targets a memory the caller doesn't own,
    or an add is malformed (session scope without a session_id)."""


@dataclass(frozen=True)
class MemoryRow:
    id: int
    scope: str
    session_id: int | None
    content: str
    created_at: str
    updated_at: str


_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _fts_match(query: str) -> str | None:
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


async def add_memory(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    content: str,
    scope: Scope,
) -> int:
    """Insert a new memory row and return its id.

    Raises :class:`MemoryScopeError` when ``scope='session'`` and no
    ``session_id`` is supplied (session-scoped rows need an owner).
    Global memories always store ``session_id=NULL``.
    """
    gate = classify_memory_safety(content)
    if not gate["save"]:
        raise MemoryGateRefusal(str(gate["reason"]))
    bound: int | None = None if scope == "global" else session_id
    if scope == "session" and bound is None:
        raise MemoryScopeError("session-scoped memory requires a session_id")
    await conn.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES (?, ?, ?)",
        (scope, bound, content),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def recall_memories(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    query: str,
    scope: RecallScope = "both",
    limit: int = 5,
) -> list[MemoryRow]:
    """Full-text search over the memories FTS index.

    ``scope='both'`` returns global memories PLUS the current session's
    memories (other sessions' entries are excluded regardless).
    Returns an empty list when the query tokenises to nothing.
    """
    match = _fts_match(query)
    if match is None:
        return []

    params: tuple[object, ...]
    if scope == "session":
        where = "m.scope = 'session' AND m.session_id = ?"
        params = (match, session_id, limit)
    elif scope == "global":
        where = "m.scope = 'global'"
        params = (match, limit)
    else:  # "both"
        where = "(m.scope = 'global' OR (m.scope = 'session' AND m.session_id = ?))"
        params = (match, session_id, limit)

    sql = (
        "SELECT m.id, m.scope, m.session_id, m.content, m.created_at, m.updated_at "
        "FROM memories_fts f JOIN memories m ON m.id = f.rowid "
        f"WHERE memories_fts MATCH ? AND {where} ORDER BY rank LIMIT ?"
    )
    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [MemoryRow(*r) for r in rows]


async def _owned_or_raise(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    memory_id: int,
) -> None:
    """Verify the caller may mutate ``memory_id``.

    Global memories are accessible from any session (any session may edit
    or delete a global note).  Session-scoped memories are owned by exactly
    one session; touching them from another raises :class:`MemoryScopeError`.
    """
    async with conn.execute(
        "SELECT scope, session_id FROM memories WHERE id = ?", (memory_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise MemoryScopeError(f"memory {memory_id} not found")
    scope, owner = row
    if scope == "session" and owner != session_id:
        raise MemoryScopeError(
            f"memory {memory_id} belongs to another session; cannot modify"
        )


async def edit_memory(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    memory_id: int,
    content: str,
) -> None:
    """Replace the content of an existing memory.

    Raises :class:`MemoryScopeError` if ``memory_id`` belongs to a different
    session (ownership guard for session-scoped rows; global rows are
    editable from any session).
    """
    await _owned_or_raise(conn, session_id=session_id, memory_id=memory_id)
    await conn.execute(
        "UPDATE memories SET content = ?, updated_at = datetime('now') WHERE id = ?",
        (content, memory_id),
    )
    await conn.commit()


async def forget_memory(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    memory_id: int,
) -> None:
    """Delete a memory row.

    Raises :class:`MemoryScopeError` if ``memory_id`` belongs to a different
    session (same ownership rule as :func:`edit_memory`).
    """
    await _owned_or_raise(conn, session_id=session_id, memory_id=memory_id)
    await conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    await conn.commit()


async def _detect_conflict(
    conn: aiosqlite.Connection,
    new_content: str,
    scope: Scope,
    session_id: int | None,
    adapter: LlmAdapter | None,
    model: str,
) -> int | None:
    """Ask the LLM whether ``new_content`` supersedes an existing active memory.

    Short-circuits to ``None`` (no conflict) when:
    * ``adapter`` is ``None``
    * there are no existing active same-scope memories

    Fails open — any exception (LLM unavailable, JSON parse error, missing
    key) returns ``None`` so the add always succeeds even without a key.
    """
    if adapter is None:
        return None

    # Fetch active same-scope memories.
    if scope == "session":
        where = "scope = 'session' AND session_id = ? AND status = 'active'"
        params: tuple[object, ...] = (session_id,)
    else:
        where = "scope = 'global' AND status = 'active'"
        params = ()

    async with conn.execute(
        f"SELECT id, content FROM memories WHERE {where}", params
    ) as cur:
        existing = await cur.fetchall()

    if not existing:
        return None

    existing_text = "\n".join(f"[{row[0]}] {row[1]}" for row in existing)

    try:
        parts: list[str] = []
        async for tok in adapter.stream(
            slot="memory_conflict/v1",
            variables={
                "new_content": new_content,
                "scope": scope,
                "existing_memories": existing_text,
            },
            model=model,
        ):
            parts.append(tok)
        raw = "".join(parts).strip()
        # Strip markdown code fences (e.g. ```json\n{...}\n```).
        if raw.startswith("```"):
            raw = raw.lstrip("`")
            if "\n" in raw:
                first, rest = raw.split("\n", 1)
                raw = rest if first.strip().lower() in ("json", "") else first + "\n" + rest
            raw = raw.rstrip("`").strip()
        parsed = json.loads(raw)
        conflict_id = parsed.get("conflict_id")
        if conflict_id is None:
            return None
        return int(conflict_id)
    except Exception:  # noqa: BLE001 — fail-open: LLM unavailable or parse error
        return None


async def add_memory_with_supersede(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    content: str,
    scope: Scope,
    adapter: LlmAdapter | None,
    model: str,
) -> int:
    """Insert a new memory with optional LLM-driven conflict detection.

    Steps:
    1. Run :func:`~paperhub.agents.memory_gate.classify_memory_safety` gate
       (raises :class:`MemoryGateRefusal` on rejection).
    2. Run :func:`_detect_conflict` — short-circuits to ``None`` when
       ``adapter`` is ``None`` or no existing memories exist, fails open on
       any LLM error.
    3. INSERT the new memory with ``supersedes=conflict_id``.
    4. If a conflict was detected, flip the old row to
       ``status='superseded'`` + ``superseded_by=<new-id>``.

    Returns the new memory's ``id``.
    """
    gate = classify_memory_safety(content)
    if not gate["save"]:
        raise MemoryGateRefusal(str(gate["reason"]))
    bound: int | None = None if scope == "global" else session_id
    if scope == "session" and bound is None:
        raise MemoryScopeError("session-scoped memory requires a session_id")

    conflict_id = await _detect_conflict(conn, content, scope, session_id, adapter, model)

    await conn.execute(
        "INSERT INTO memories (scope, session_id, content, supersedes) VALUES (?, ?, ?, ?)",
        (scope, bound, content, conflict_id),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    new_id = int(row[0])

    if conflict_id is not None:
        await conn.execute(
            "UPDATE memories SET status = 'superseded', superseded_by = ?, updated_at = datetime('now') WHERE id = ?",
            (new_id, conflict_id),
        )
    await conn.commit()

    return new_id
