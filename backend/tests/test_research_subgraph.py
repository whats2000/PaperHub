"""Tests for the paper_qa subgraph topology (Plan C v2.10-4).

Verifies the agentic-hierarchical pipeline:

    pq_resolve → conditional {empty, dispatch} → pq_finalize → END

Replaces the old map/synthesize/single topology tests.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from paperhub.agents.paper_qa_subagent import PerPaperPicks, PickedChunk

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _make_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _insert_paper(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    arxiv_id: str,
    title: str,
) -> int:
    """Insert a paper_content + papers row and return paper_content_id."""
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path, sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:{arxiv_id}", "arxiv", arxiv_id, title,
            "[]", 2024, "abstract",
            "/tmp/x.tex", "/tmp", "/tmp/x.html",
            json.dumps([{
                "name": "Method", "char_start": 0, "char_end": 100,
                "token_count": 50, "chunk_count": 1,
            }]),
        ),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])

    # Insert one chunk so the subagent has something to read.
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
        "VALUES (?, 'Method', 0, 100, ?)",
        (pcid, f"Content of {title}."),
    )
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (?, ?, 1)",
        (session_id, pcid),
    )
    await conn.commit()
    return pcid


# ---------------------------------------------------------------------------
# Adapter stub
# ---------------------------------------------------------------------------


class _StubAdapter:
    """Minimal adapter whose stream yields canned tokens."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self.calls: list[dict[str, Any]] = []

    async def structured(self, **_: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def stream(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        model: str,  # noqa: ARG002
        history: list[dict[str, str]] | None = None,  # noqa: ARG002
        **_: Any,
    ) -> AsyncIterator[str]:
        self.calls.append({"slot": slot, "variables": dict(variables)})
        tokens = list(self._tokens)

        async def _gen() -> AsyncIterator[str]:
            for t in tokens:
                yield t

        return _gen()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_paper_qa_subgraph_empty_session_resolves_to_pq_empty(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """No enabled papers → pq_resolve branches to pq_empty;
    final_response contains the no-references message."""
    from unittest.mock import MagicMock

    from paperhub.agents.research_graph import ResearchDeps, build_paper_qa_subgraph
    from paperhub.mcp.registry import MCPRegistry
    from paperhub.pipelines.paper_pipeline import PaperPipeline

    session_id = await _make_session(migrated_db)

    adapter = _StubAdapter(["should not stream"])
    deps = ResearchDeps(
        adapter=adapter,  # type: ignore[arg-type]
        tracer=fake_tracer,
        paper_qa_model="stub",
        conn=migrated_db,
        pipeline=MagicMock(spec=PaperPipeline),
        mcp_registry=MagicMock(spec=MCPRegistry),
        paper_qa_subagent_model="stub",
        paper_qa_max_section_reads=2,
    )
    graph = build_paper_qa_subgraph(deps)

    state: dict[str, Any] = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "",
        "session_id": session_id,
        "user_message": "what is this about?",
        "history": [],
    }

    final_state: dict[str, Any] = {}
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "values" and isinstance(payload, dict):
            final_state = payload

    assert "final_response" in final_state
    assert "No references are enabled" in final_state["final_response"]
    # No subagent calls.
    assert not adapter.calls


async def test_paper_qa_subgraph_dispatches_one_subagent_per_enabled_paper(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """pq_dispatch fans out per asyncio.gather; one PerPaperPicks per paper
    lands in state['pq_per_paper_picks'] before pq_finalize runs."""
    from unittest.mock import MagicMock

    from paperhub.agents.research_graph import ResearchDeps, build_paper_qa_subgraph
    from paperhub.mcp.registry import MCPRegistry
    from paperhub.pipelines.paper_pipeline import PaperPipeline

    session_id = await _make_session(migrated_db)
    pcid_a = await _insert_paper(
        migrated_db, session_id=session_id, arxiv_id="2401.0100", title="Paper A",
    )
    pcid_b = await _insert_paper(
        migrated_db, session_id=session_id, arxiv_id="2401.0101", title="Paper B",
    )

    # Canned PerPaperPicks returned by the stubbed subagent.
    canned: dict[int, PerPaperPicks] = {
        pcid_a: PerPaperPicks(
            paper_content_id=pcid_a, title="Paper A",
            picked_chunks=[PickedChunk(chunk_id=1, text="A content.", section="Method")],
            rationale="Paper A focuses on X.",
        ),
        pcid_b: PerPaperPicks(
            paper_content_id=pcid_b, title="Paper B",
            picked_chunks=[PickedChunk(chunk_id=2, text="B content.", section="Method")],
            rationale="Paper B focuses on Y.",
        ),
    }
    dispatch_calls: list[int] = []

    async def _fake_subagent(*, paper_content_id: int, **_: Any) -> PerPaperPicks:
        dispatch_calls.append(paper_content_id)
        return canned[paper_content_id]

    # Stub the finalizer adapter to return canned tokens.
    finalize_tokens = ["Synthesis: A [chunk:1] vs B [chunk:2]."]
    adapter = _StubAdapter(finalize_tokens)

    deps = ResearchDeps(
        adapter=adapter,  # type: ignore[arg-type]
        tracer=fake_tracer,
        paper_qa_model="stub",
        conn=migrated_db,
        pipeline=MagicMock(spec=PaperPipeline),
        mcp_registry=MagicMock(spec=MCPRegistry),
        paper_qa_subagent_model="stub",
        paper_qa_max_section_reads=2,
    )
    graph = build_paper_qa_subgraph(deps)

    state: dict[str, Any] = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "",
        "session_id": session_id,
        "user_message": "compare A and B",
        "history": [],
    }

    final_state: dict[str, Any] = {}
    with patch(
        "paperhub.agents.research_graph.run_paper_qa_subagent",
        new=AsyncMock(side_effect=_fake_subagent),
    ):
        async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
            if mode == "values" and isinstance(payload, dict):
                final_state = payload

    # Both subagents were called.
    assert set(dispatch_calls) == {pcid_a, pcid_b}, (
        f"Expected subagent calls for both papers; got: {dispatch_calls}"
    )

    # final_response carries the synthesis text.
    assert "final_response" in final_state
    assert final_state["final_response"]

    # pq_per_paper_picks has 2 entries.
    picks = final_state.get("pq_per_paper_picks") or []
    assert len(picks) == 2
    assert {p.paper_content_id for p in picks} == {pcid_a, pcid_b}


async def test_paper_search_emits_every_step_under_out_of_order_completion(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Regression (trace-panel drop bug): _ps_process must emit a tool_step
    custom event for EVERY tracer row, even when parallel Discover/Resolve
    calls commit out-of-order.

    The tracer assigns step_index at OPEN time but commits at CLOSE time. With
    a monotonic ``last_step`` watermark, a request that opened first (low
    index) but finished slowest commits AFTER the watermark already advanced
    past it — so its row is never streamed and is missing from the trace
    panel (the missing #10/#12 in real runs). The set-based dedup must stream
    every row exactly once.
    """
    import asyncio
    from unittest.mock import MagicMock

    from paperhub.agents.research_graph import (
        ResearchDeps,
        build_paper_search_subgraph,
    )
    from paperhub.agents.research_pipeline import (
        CanonicalIdentity,
        ParsedRequest,
        ResolvedPaper,
    )

    # First-opened request finishes SLOWEST → forces out-of-order commit.
    latency_by_hint = {"p0": 0.30, "p1": 0.05, "p2": 0.10, "p3": 0.15}
    requests = [
        ParsedRequest(hint=h, kind="natural_language") for h in latency_by_hint
    ]

    async def _fake_parse(*_: Any, **__: Any) -> list[ParsedRequest]:
        return list(requests)

    async def _fake_discover(
        request: ParsedRequest, *, tracer: Any, model: str, **__: Any,
    ) -> CanonicalIdentity:
        async with tracer.step(
            agent="research", tool="paper_search:discover_plan", model=model,
        ):
            await asyncio.sleep(latency_by_hint[request.hint])
        return CanonicalIdentity(
            title=request.hint, author_surname=None, year=2024, confidence="high",
        )

    async def _fake_resolve(
        request: ParsedRequest, identity: CanonicalIdentity, *, tracer: Any, **__: Any,
    ) -> ResolvedPaper:
        async with tracer.step(
            agent="research", tool="paper_search:paperhub.search_web", model=None,
        ):
            await asyncio.sleep(0.02)
        return ResolvedPaper(
            request=request, identity=identity,
            paper_id=f"ss:{request.hint}", meta={"title": request.hint},
        )

    async def _fake_synth(*_: Any, **__: Any) -> str:
        return "prose"

    deps = ResearchDeps(
        adapter=MagicMock(),
        tracer=fake_tracer,
        paper_qa_model="stub",
        conn=migrated_db,
        pipeline=MagicMock(),
        mcp_registry=MagicMock(),
    )

    emitted: list[int] = []
    with (
        patch("paperhub.agents.research_graph.parse_user_message", new=_fake_parse),
        patch("paperhub.agents.research_graph.discover_canonical", new=_fake_discover),
        patch("paperhub.agents.research_graph.resolve_via_ss", new=_fake_resolve),
        patch("paperhub.agents.research_graph.synthesize_prose", new=_fake_synth),
    ):
        graph = build_paper_search_subgraph(deps)
        state: dict[str, Any] = {
            "run_id": fake_tracer._run_id,  # noqa: SLF001
            "branch": "",
            "session_id": 1,
            "user_message": "find p0 p1 p2 p3",
            "history": [],
            "ps_last_step_index": -1,
        }
        async for mode, payload in graph.astream(
            state, stream_mode=["custom", "values"],
        ):
            if mode == "custom" and payload.get("event") == "tool_step":
                emitted.append(int(payload["record"]["step_index"]))

    # Every tracer row written for this run must have been streamed exactly once.
    async with migrated_db.execute(
        "SELECT step_index FROM tool_calls WHERE run_id = ? ORDER BY step_index",
        (fake_tracer._run_id,),  # noqa: SLF001
    ) as cur:
        all_indices = sorted(int(r[0]) for r in await cur.fetchall())

    assert sorted(emitted) == all_indices, (
        f"Steps dropped from trace stream. DB rows={all_indices}, "
        f"emitted={sorted(emitted)}, missing={sorted(set(all_indices) - set(emitted))}"
    )
    assert len(emitted) == len(set(emitted)), (
        f"Duplicate tool_step events emitted: {emitted}"
    )


async def test_paper_search_dedups_candidates_with_same_paper_id(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """Regression: the Resolver can land the SAME paper from two different
    angles/queries, which previously emitted duplicate cards (3 slots, 2 real
    papers). _ps_finalize must dedup candidates by paper_id (first wins)."""
    import asyncio
    from unittest.mock import MagicMock

    from paperhub.agents.research_graph import (
        ResearchDeps,
        build_paper_search_subgraph,
    )
    from paperhub.agents.research_pipeline import (
        CanonicalIdentity,
        ParsedRequest,
        ResolvedPaper,
    )

    # p0 and p2 resolve to the SAME paper_id (the duplicate); p1 is distinct.
    paper_id_by_hint = {"p0": "ss:dup", "p1": "ss:unique", "p2": "ss:dup"}
    requests = [
        ParsedRequest(hint=h, kind="natural_language") for h in paper_id_by_hint
    ]

    async def _fake_parse(*_: Any, **__: Any) -> list[ParsedRequest]:
        return list(requests)

    async def _fake_discover(
        request: ParsedRequest, *, tracer: Any, model: str, **__: Any,
    ) -> CanonicalIdentity:
        async with tracer.step(
            agent="research", tool="paper_search:discover_plan", model=model,
        ):
            await asyncio.sleep(0)
        return CanonicalIdentity(
            title=request.hint, author_surname=None, year=2024, confidence="high",
        )

    async def _fake_resolve(
        request: ParsedRequest, identity: CanonicalIdentity, *, tracer: Any, **__: Any,
    ) -> ResolvedPaper:
        async with tracer.step(
            agent="research", tool="paper_search:paperhub.search_web", model=None,
        ):
            await asyncio.sleep(0)
        pid = paper_id_by_hint[request.hint]
        return ResolvedPaper(
            request=request, identity=identity,
            paper_id=pid, meta={"title": pid},
        )

    async def _fake_synth(*_: Any, **__: Any) -> str:
        return "prose"

    deps = ResearchDeps(
        adapter=MagicMock(),
        tracer=fake_tracer,
        paper_qa_model="stub",
        conn=migrated_db,
        pipeline=MagicMock(),
        mcp_registry=MagicMock(),
    )

    candidates: list[dict[str, Any]] = []
    with (
        patch("paperhub.agents.research_graph.parse_user_message", new=_fake_parse),
        patch("paperhub.agents.research_graph.discover_canonical", new=_fake_discover),
        patch("paperhub.agents.research_graph.resolve_via_ss", new=_fake_resolve),
        patch("paperhub.agents.research_graph.synthesize_prose", new=_fake_synth),
    ):
        graph = build_paper_search_subgraph(deps)
        state: dict[str, Any] = {
            "run_id": fake_tracer._run_id,  # noqa: SLF001
            "branch": "",
            "session_id": 1,
            "user_message": "find p0 p1 p2",
            "history": [],
            "ps_last_step_index": -1,
        }
        async for mode, payload in graph.astream(
            state, stream_mode=["custom", "values"],
        ):
            if mode == "custom" and payload.get("event") == "search_results":
                candidates = payload["candidates"]

    paper_ids = [c.paper_id for c in candidates]
    assert paper_ids == ["ss:dup", "ss:unique"], (
        f"Expected deduped [ss:dup, ss:unique] (first occurrence wins), got {paper_ids}"
    )
    assert len(paper_ids) == len(set(paper_ids)), f"Duplicate cards emitted: {paper_ids}"


async def test_paper_qa_subgraph_all_empty_picks_yields_no_content_message(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Any,
) -> None:
    """When every subagent returns empty picked_chunks, pq_finalize short-circuits
    with the no-content message (no adapter stream call)."""
    from unittest.mock import MagicMock

    from paperhub.agents.research_graph import ResearchDeps, build_paper_qa_subgraph
    from paperhub.mcp.registry import MCPRegistry
    from paperhub.pipelines.paper_pipeline import PaperPipeline

    session_id = await _make_session(migrated_db)
    await _insert_paper(
        migrated_db, session_id=session_id, arxiv_id="2401.0200", title="Empty Paper",
    )

    async def _empty_subagent(*, paper_content_id: int, **_: Any) -> PerPaperPicks:
        return PerPaperPicks(
            paper_content_id=paper_content_id, title="Empty Paper",
            picked_chunks=[],
            rationale="Nothing found.",
        )

    adapter = _StubAdapter(["should not stream"])
    deps = ResearchDeps(
        adapter=adapter,  # type: ignore[arg-type]
        tracer=fake_tracer,
        paper_qa_model="stub",
        conn=migrated_db,
        pipeline=MagicMock(spec=PaperPipeline),
        mcp_registry=MagicMock(spec=MCPRegistry),
        paper_qa_subagent_model="stub",
        paper_qa_max_section_reads=2,
    )
    graph = build_paper_qa_subgraph(deps)

    state: dict[str, Any] = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "",
        "session_id": session_id,
        "user_message": "anything",
        "history": [],
    }

    final_state: dict[str, Any] = {}
    with patch(
        "paperhub.agents.research_graph.run_paper_qa_subagent",
        new=AsyncMock(side_effect=_empty_subagent),
    ):
        async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
            if mode == "values" and isinstance(payload, dict):
                final_state = payload

    # No adapter.stream calls (short-circuit).
    assert not adapter.calls, f"Adapter should not be called; got: {adapter.calls}"
    # final_response is the no-content sentinel.
    assert "final_response" in final_state
    assert "I checked every enabled reference" in final_state["final_response"]
