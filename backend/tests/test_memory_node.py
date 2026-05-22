import json

import aiosqlite
import pytest

from paperhub.agents.memory_node import memory_node
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


class _FakeRegistry:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def call(self, name: str, args: dict) -> object:
        from paperhub.agents import memory_tools as mt

        if name == "memory.add":
            mid = await mt.add_memory(
                self.conn,
                session_id=1,
                content=args["content"],
                scope=args["scope"],
            )
            return {"id": mid}
        if name == "memory.recall":
            hits = await mt.recall_memories(
                self.conn, session_id=1, query=args["query"], scope="both"
            )
            return [{"id": h.id, "scope": h.scope, "content": h.content} for h in hits]
        raise AssertionError(name)


class _FullRegistry:
    """Registry that delegates add/recall/edit/forget to memory_tools (real DB ops)."""

    def __init__(self, conn: aiosqlite.Connection, session_id: int = 1) -> None:
        self.conn = conn
        self.session_id = session_id

    async def call(self, name: str, args: dict) -> object:
        from paperhub.agents import memory_tools as mt

        if name == "memory.add":
            mid = await mt.add_memory(
                self.conn,
                session_id=self.session_id,
                content=args["content"],
                scope=args["scope"],
            )
            return {"id": mid}
        if name == "memory.recall":
            hits = await mt.recall_memories(
                self.conn,
                session_id=self.session_id,
                query=args["query"],
                scope="both",
            )
            return [{"id": h.id, "scope": h.scope, "content": h.content} for h in hits]
        if name == "memory.edit":
            await mt.edit_memory(
                self.conn,
                session_id=self.session_id,
                memory_id=args["memory_id"],
                content=args["content"],
            )
            return {"updated": True}
        if name == "memory.forget":
            await mt.forget_memory(
                self.conn,
                session_id=self.session_id,
                memory_id=args["memory_id"],
            )
            return {"deleted": True}
        raise AssertionError(name)


class _RejectedAddRegistry:
    """Registry whose memory.add returns a JSON-string rejection (exercises _normalize)."""

    async def call(self, name: str, args: dict) -> object:
        if name == "memory.add":
            # Return a JSON string (real MCP wire shape) — exercises _normalize
            return json.dumps({"error": "rejected", "reason": "scope violation"})
        raise AssertionError(name)


class _NoMatchRecallRegistry:
    """Registry whose memory.recall always returns an empty list."""

    async def call(self, name: str, args: dict) -> object:
        if name == "memory.recall":
            return []
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_memory_node_add_persists_and_confirms(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "remember I'm comparing MoE routing papers",
        "effective_query": "remember I'm comparing MoE routing papers",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock='{"op":"add","scope":"session","content":"comparing MoE routing papers","target":"","confirmation":"Noted — I will remember that."}',
    )
    assert "final_response" in out and out["final_response"]
    async with migrated_db.execute("SELECT content FROM memories") as cur:
        rows = await cur.fetchall()
    assert rows and "MoE routing" in rows[0][0]


@pytest.mark.asyncio
async def test_memory_node_unknown_op_returns_fallback(
    migrated_db: aiosqlite.Connection,
) -> None:
    """When the LLM returns an unrecognised op, the node should return a graceful message."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "do something weird",
        "effective_query": "do something weird",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock='{"op":"unknown","scope":"session","content":"x","target":"","confirmation":"ok"}',
    )
    assert "final_response" in out
    assert "rephrase" in out["final_response"].lower()


@pytest.mark.asyncio
async def test_memory_node_confirmation_in_state(
    migrated_db: aiosqlite.Connection,
) -> None:
    """The confirmation string from the LLM op JSON is forwarded as final_response."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "remember I prefer dark mode",
        "effective_query": "remember I prefer dark mode",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock='{"op":"add","scope":"session","content":"prefers dark mode","target":"","confirmation":"Got it, I will remember that you prefer dark mode."}',
    )
    assert out["final_response"] == "Got it, I will remember that you prefer dark mode."


# ── NFR-05 write-surface coverage ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_node_edit_persists_and_confirms(
    migrated_db: aiosqlite.Connection,
) -> None:
    """edit op: recall finds the seeded row, edit_memory updates it, confirmation returned."""
    from paperhub.agents import memory_tools as mt

    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    # Seed a session-1 memory the node can recall.
    await mt.add_memory(migrated_db, session_id=1, content="original content", scope="session")

    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "update my note",
        "effective_query": "update my note",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FullRegistry(migrated_db, session_id=1),
        model="gpt-4o-mini",
        op_mock=(
            '{"op":"edit","scope":"session","content":"updated content",'
            '"target":"original","confirmation":"Updated."}'
        ),
    )
    assert out["final_response"] == "Updated."
    async with migrated_db.execute("SELECT content FROM memories") as cur:
        rows = await cur.fetchall()
    assert rows and rows[0][0] == "updated content"


@pytest.mark.asyncio
async def test_memory_node_forget_removes_row_and_confirms(
    migrated_db: aiosqlite.Connection,
) -> None:
    """forget op: recall finds the seeded row, forget_memory deletes it, confirmation returned."""
    from paperhub.agents import memory_tools as mt

    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    # Seed a session-1 memory to forget.
    await mt.add_memory(migrated_db, session_id=1, content="throwaway note", scope="session")

    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "forget that note",
        "effective_query": "forget that note",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FullRegistry(migrated_db, session_id=1),
        model="gpt-4o-mini",
        op_mock=(
            '{"op":"forget","scope":"session","content":"",'
            '"target":"throwaway","confirmation":"Forgotten."}'
        ),
    )
    assert out["final_response"] == "Forgotten."
    async with migrated_db.execute("SELECT COUNT(*) FROM memories") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 0


@pytest.mark.asyncio
async def test_memory_node_rejected_surfaces_reason_not_confirmation(
    migrated_db: aiosqlite.Connection,
) -> None:
    """MOST IMPORTANT: a rejected MCP result must NOT leak the confirmation.

    The node should surface the rejection reason, and a tool_calls row with
    status='rejected' must be written for the run (NFR-05 observability).
    The _RejectedAddRegistry returns a JSON *string* to exercise _normalize.
    """
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()

    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "remember my API key",
        "effective_query": "remember my API key",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_RejectedAddRegistry(),
        model="gpt-4o-mini",
        op_mock=(
            '{"op":"add","scope":"session","content":"my API key",'
            '"target":"","confirmation":"Saved."}'
        ),
    )
    # Confirmation must NOT be returned — the rejection must surface instead.
    assert out["final_response"] != "Saved.", (
        "BUG: confirmation leaked through despite rejected MCP result"
    )
    assert "scope violation" in out["final_response"], (
        f"Expected rejection reason in response, got: {out['final_response']!r}"
    )
    # A tool_calls row with status='rejected' must have been written.
    async with migrated_db.execute(
        "SELECT status FROM tool_calls WHERE run_id = 1 AND status = 'rejected'"
    ) as cur:
        rejected_rows = await cur.fetchall()
    assert rejected_rows, (
        "BUG: no tool_calls row with status='rejected' found for the run"
    )


@pytest.mark.asyncio
async def test_memory_node_no_match_returns_not_found_message(
    migrated_db: aiosqlite.Connection,
) -> None:
    """edit op where recall returns empty list → 'couldn't find a matching note' message."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()

    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "update the nonexistent note",
        "effective_query": "update the nonexistent note",
        "response_language": "English",
    }
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_NoMatchRecallRegistry(),
        model="gpt-4o-mini",
        op_mock=(
            '{"op":"edit","scope":"session","content":"new content",'
            '"target":"nonexistent","confirmation":"Updated."}'
        ),
    )
    # Must contain the no-match message from memory_node.py line 106.
    assert "couldn't find a matching note" in out["final_response"].lower(), (
        f"Expected no-match message, got: {out['final_response']!r}"
    )


# ── Bug 1 regression: fenced JSON from real LLMs ─────────────────────────────


@pytest.mark.asyncio
async def test_memory_node_add_fenced_json_parses(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Bug 1 regression: when the LLM wraps its JSON response in ```json fences,
    op-extraction must still parse it — no JSONDecodeError.

    The real Gemini wire shape:
        ```json
        {"op":"add","scope":"session","content":"x","target":"","confirmation":"ok"}
        ```
    """
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "remember I'm testing fenced JSON",
        "effective_query": "remember I'm testing fenced JSON",
        "response_language": "English",
    }
    fenced_op = (
        "```json\n"
        '{"op":"add","scope":"session","content":"fenced content test","target":"",'
        '"confirmation":"Noted — fenced JSON parsed correctly."}\n'
        "```"
    )
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock=fenced_op,
    )
    # Must not raise JSONDecodeError and must return the confirmation.
    assert out.get("final_response") == "Noted — fenced JSON parsed correctly.", (
        f"Expected confirmation, got: {out.get('final_response')!r}"
    )
    # Memory must have been persisted.
    async with migrated_db.execute("SELECT content FROM memories") as cur:
        rows = await cur.fetchall()
    assert rows and "fenced content test" in rows[0][0]


@pytest.mark.asyncio
async def test_memory_node_add_bare_fence_parses(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Bug 1 regression: triple-backtick without a language tag must also parse."""
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "remember bare fence test",
        "effective_query": "remember bare fence test",
        "response_language": "English",
    }
    bare_fenced_op = (
        "```\n"
        '{"op":"add","scope":"session","content":"bare fence content","target":"",'
        '"confirmation":"Saved bare."}\n'
        "```"
    )
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=_FakeRegistry(migrated_db),
        model="gpt-4o-mini",
        op_mock=bare_fenced_op,
    )
    assert out.get("final_response") == "Saved bare.", (
        f"Expected confirmation, got: {out.get('final_response')!r}"
    )
    async with migrated_db.execute("SELECT content FROM memories") as cur:
        rows = await cur.fetchall()
    assert rows and "bare fence content" in rows[0][0]


# ── Bug 2 regression: recall result is {"result":[...]} envelope ─────────────


class _EnvelopeRecallRegistry:
    """Registry whose memory.recall returns the FastMCP list-return envelope.

    This is the real MCP wire shape when FastMCP wraps a list return in
    {"result": [...]}.  The existing tests used a plain list; this exercises
    the envelope-unwrapping path added in Bug 2 fix.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn
        self.edit_called = False
        self.forget_called = False

    async def call(self, name: str, args: dict) -> object:
        from paperhub.agents import memory_tools as mt

        if name == "memory.recall":
            hits = await mt.recall_memories(
                self.conn, session_id=1, query=args["query"], scope="both"
            )
            raw_list = [{"id": h.id, "scope": h.scope, "content": h.content} for h in hits]
            # Simulate FastMCP's {"result": [...]} envelope for list returns.
            return {"result": raw_list}
        if name == "memory.edit":
            self.edit_called = True
            await mt.edit_memory(
                self.conn, session_id=1, memory_id=args["memory_id"], content=args["content"]
            )
            return {"ok": True}
        if name == "memory.forget":
            self.forget_called = True
            await mt.forget_memory(self.conn, session_id=1, memory_id=args["memory_id"])
            return {"ok": True}
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_memory_node_edit_with_envelope_recall(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Bug 2 regression: edit op where recall returns {"result":[...]} envelope.

    The node must unwrap the envelope and find the matching memory — NOT return
    'couldn't find a matching note'.
    """
    from paperhub.agents import memory_tools as mt

    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    await mt.add_memory(migrated_db, session_id=1, content="envelope edit target", scope="session")

    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "update that note",
        "effective_query": "update that note",
        "response_language": "English",
    }
    reg = _EnvelopeRecallRegistry(migrated_db)
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=reg,
        model="gpt-4o-mini",
        op_mock=(
            '{"op":"edit","scope":"session","content":"envelope edited content",'
            '"target":"envelope edit target","confirmation":"Envelope edit confirmed."}'
        ),
    )
    # Confirm the edit confirmation was returned (not the no-match fallback).
    assert out["final_response"] == "Envelope edit confirmed.", (
        f"Bug 2: envelope not unwrapped; got: {out['final_response']!r}"
    )
    assert reg.edit_called, "Bug 2: memory.edit was never called"
    async with migrated_db.execute("SELECT content FROM memories") as cur:
        rows = await cur.fetchall()
    assert rows and rows[0][0] == "envelope edited content"


@pytest.mark.asyncio
async def test_memory_node_forget_with_envelope_recall(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Bug 2 regression: forget op where recall returns {"result":[...]} envelope.

    The node must unwrap the envelope and call memory.forget — NOT return
    'couldn't find a matching note'.
    """
    from paperhub.agents import memory_tools as mt

    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    await mt.add_memory(migrated_db, session_id=1, content="envelope forget target", scope="session")

    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1,
        "session_id": 1,
        "user_message": "forget that note",
        "effective_query": "forget that note",
        "response_language": "English",
    }
    reg = _EnvelopeRecallRegistry(migrated_db)
    out = await memory_node(
        state,
        adapter=LiteLlmAdapter(),
        tracer=tracer,
        registry=reg,
        model="gpt-4o-mini",
        op_mock=(
            '{"op":"forget","scope":"session","content":"",'
            '"target":"envelope forget target","confirmation":"Envelope forget confirmed."}'
        ),
    )
    assert out["final_response"] == "Envelope forget confirmed.", (
        f"Bug 2: envelope not unwrapped; got: {out['final_response']!r}"
    )
    assert reg.forget_called, "Bug 2: memory.forget was never called"
    async with migrated_db.execute("SELECT COUNT(*) FROM memories") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 0
