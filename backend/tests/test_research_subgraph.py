"""Multi-node Research subgraph tests (Plan C v4).

Verifies the actual LangGraph topology (cyclic paper_search loop + paper_qa
count-branch + outer dispatcher) — NOT a 2-node passthrough wrapper. Each
test invokes the compiled subgraph via
``astream(state, stream_mode=["custom", "values"])`` and asserts on the
custom-stream payloads + final state.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from paperhub.agents.research import (
    MAX_TOOL_ITERATIONS,
)
from paperhub.agents.research_graph import (
    ResearchDeps,
    build_paper_qa_subgraph,
    build_paper_search_subgraph,
    build_research_subgraph,
)
from paperhub.agents.research_tools import (
    LibraryHit,
    SemanticScholarToolHit,
)
from paperhub.models.domain import RoutingDecision
from paperhub.rag.retriever import RetrievedChunk, Retriever
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


def _decision(intent: str) -> RoutingDecision:
    return RoutingDecision(
        intent=intent,  # type: ignore[arg-type]
        model_tier="flagship",
        confidence=0.95,
        reasoning="test",
    )


def _msg(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    m: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _candidates_block(picks: list[dict[str, Any]]) -> str:
    return "```json:candidates\n" + json.dumps(picks) + "\n```"


async def _collect(
    graph: Any, state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drive ``graph.astream`` with ``stream_mode=["custom", "values"]``;
    return ``(custom_payloads, final_state)``."""
    customs: list[dict[str, Any]] = []
    final_state: dict[str, Any] = {}
    async for mode, payload in graph.astream(
        state, stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            customs.append(payload)
        elif mode == "values" and isinstance(payload, dict):
            final_state = payload
    return customs, final_state


def _deps(
    *,
    conn: aiosqlite.Connection,
    tracer: Tracer,
    pipeline: Any | None = None,
    retriever: Any | None = None,
    adapter: Any | None = None,
) -> ResearchDeps:
    return ResearchDeps(
        adapter=adapter if adapter is not None else MagicMock(),
        tracer=tracer,
        paper_qa_model="m",
        conn=conn,
        pipeline=pipeline if pipeline is not None else MagicMock(),
        retriever=retriever if retriever is not None else MagicMock(spec=Retriever),
    )


# ---------------------------------------------------------------------------
# paper_search subgraph — cyclic plan ↔ dispatch loop
# ---------------------------------------------------------------------------


async def test_paper_search_subgraph_loops_until_no_tool_calls(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    """ps_plan returns tool_calls → ps_dispatch_tools runs → ps_plan again →
    returns final content. Iteration count proves the loop fires twice."""
    seq = [
        _msg(tool_calls=[
            _tool_call("c1", "search_library", {"query": "transformers"}),
        ]),
        _msg(content="No picks."),
    ]
    comp = AsyncMock(side_effect=seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=[])):
        graph = build_paper_search_subgraph(
            _deps(conn=migrated_db, tracer=fake_tracer, pipeline=fake_pipeline),
        )
        state = {
            "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
            "session_id": 1, "user_message": "find transformers",
        }
        customs, final_state = await _collect(graph, state)
    # acompletion called twice (loop iteration).
    assert comp.await_count == 2
    # 2 tool_step events emitted (plan + tool dispatch + plan), where 2 are plans
    # and 1 is the search_library dispatch — at least the plan step is streamed.
    tool_steps = [c for c in customs if c.get("event") == "tool_step"]
    assert len(tool_steps) >= 2, (
        f"Expected at least 2 tool_step events (2 plans + dispatch), got "
        f"{len(tool_steps)}"
    )
    # final_response surfaced via values stream.
    assert "No picks" in final_state.get("final_response", "")


async def test_paper_search_subgraph_caps_at_max_iterations(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    """If the model keeps returning tool_calls, ps_plan branches to
    ps_finalize at iter == MAX_TOOL_ITERATIONS rather than looping forever."""
    # Always return a tool call — the cap must kick in.
    seq = [
        _msg(tool_calls=[_tool_call(f"c{i}", "search_library", {"query": "q"})])
        for i in range(MAX_TOOL_ITERATIONS + 5)
    ]
    comp = AsyncMock(side_effect=seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=[])):
        graph = build_paper_search_subgraph(
            _deps(conn=migrated_db, tracer=fake_tracer, pipeline=fake_pipeline),
        )
        state = {
            "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
            "session_id": 1, "user_message": "spin forever",
        }
        _, final_state = await _collect(graph, state)
    # acompletion stopped at the cap — not more than MAX_TOOL_ITERATIONS.
    assert comp.await_count <= MAX_TOOL_ITERATIONS, (
        f"Plan loop blew past MAX_TOOL_ITERATIONS: {comp.await_count}"
    )
    final = final_state.get("final_response", "")
    assert "tool-call limit" in final, f"Expected cap message, got: {final!r}"


async def test_paper_search_subgraph_emits_search_results_event(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
    seed_library: int,
) -> None:
    """When the agent emits a json:candidates block, ps_finalize parses it
    and writes a ``search_results`` event via stream_writer (no DB
    finalize-cap enforcement inside the node — that's chat.py's job)."""
    lib_hits = [
        LibraryHit(
            paper_content_id=seed_library, arxiv_id="1706.03762",
            title="Attention Is All You Need", abstract="abs", year=2017,
        ),
    ]
    block = _candidates_block([
        {
            "paper_id": f"library:{seed_library}",
            "reason": "the transformer paper",
            "finalize": True,
        },
    ])
    seq = [
        _msg(tool_calls=[_tool_call("c1", "search_library", {"query": "t"})]),
        _msg(content="Found it.\n\n" + block),
    ]
    comp = AsyncMock(side_effect=seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=lib_hits)):
        graph = build_paper_search_subgraph(
            _deps(conn=migrated_db, tracer=fake_tracer, pipeline=fake_pipeline),
        )
        state = {
            "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
            "session_id": 1, "user_message": "transformer paper",
        }
        customs, final_state = await _collect(graph, state)
    search_result_evts = [c for c in customs if c.get("event") == "search_results"]
    assert len(search_result_evts) == 1
    candidates = search_result_evts[0]["candidates"]
    assert len(candidates) == 1
    assert candidates[0].paper_id == f"library:{seed_library}"
    assert candidates[0].finalize is True
    # Final response has the prose, not the JSON block.
    final = final_state.get("final_response", "")
    assert "Found it" in final
    assert "json:candidates" not in final


async def test_paper_search_subgraph_external_search_cap_inside_dispatch_node(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    """The ps_dispatch_tools node enforces the external-search cap (3)
    by short-circuiting the 4th call without touching the dispatcher."""
    seq = [
        _msg(tool_calls=[_tool_call(f"c{i}", "search_semantic_scholar",
                                    {"query": f"v{i}"})])
        for i in range(4)
    ] + [_msg(content="capped")]
    ss_dispatcher = AsyncMock(return_value=[])
    comp = AsyncMock(side_effect=seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_semantic_scholar_dispatch",
               new=ss_dispatcher):
        graph = build_paper_search_subgraph(
            _deps(conn=migrated_db, tracer=fake_tracer, pipeline=fake_pipeline),
        )
        state = {
            "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
            "session_id": 1, "user_message": "spin SS",
        }
        await _collect(graph, state)
    # Dispatcher only called 3 times — 4th was capped inside the node.
    assert ss_dispatcher.await_count == 3


# ---------------------------------------------------------------------------
# paper_qa subgraph — count branching
# ---------------------------------------------------------------------------


async def _seed_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _seed_paper_with_chunks(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    arxiv_id: str,
    title: str,
    chunk_texts: list[str],
) -> tuple[int, list[int]]:
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:{arxiv_id}", "arxiv", arxiv_id, title, "[]", 2024,
            "abs", "/tmp/x.tex", "/tmp", "/tmp/x.html",
        ),
    )
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    pcid = int(row[0])
    chunk_ids: list[int] = []
    for i, txt in enumerate(chunk_texts):
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, "
            "char_end, text) VALUES (?, ?, ?, ?, ?)",
            (pcid, "Body", i * 100, (i + 1) * 100, txt),
        )
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            cr = await cur.fetchone()
        assert cr is not None
        chunk_ids.append(int(cr[0]))
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) "
        "VALUES (?, ?, 1)",
        (session_id, pcid),
    )
    await conn.commit()
    return pcid, chunk_ids


class _StubAdapter:
    def __init__(
        self,
        tokens: list[str],
        *,
        token_map: dict[str, list[str]] | None = None,
        latency: float = 0.0,
    ) -> None:
        self._tokens = tokens
        self._token_map = token_map or {}
        self._latency = latency

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
        title = variables.get("title", "")
        if title and title in self._token_map:
            tokens = list(self._token_map[title])
        elif slot in self._token_map:
            tokens = list(self._token_map[slot])
        else:
            tokens = list(self._tokens)
        latency = self._latency

        async def _gen() -> AsyncIterator[str]:
            if latency:
                await asyncio.sleep(latency)
            for t in tokens:
                yield t

        return _gen()


async def test_paper_qa_subgraph_routes_to_empty_when_no_refs(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """No enabled papers → pq_branch returns 'empty' → pq_empty sets
    final_response sentinel without touching retriever / adapter."""
    session_id = await _seed_session(migrated_db)
    retriever = MagicMock(spec=Retriever)
    adapter = MagicMock()
    graph = build_paper_qa_subgraph(
        _deps(conn=migrated_db, tracer=fake_tracer,
              retriever=retriever, adapter=adapter),
    )
    state = {
        "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
        "session_id": session_id, "user_message": "anything",
    }
    customs, final_state = await _collect(graph, state)
    # No token events; final_response is the sentinel.
    assert [c for c in customs if c.get("event") == "token"] == []
    assert "No references are enabled" in final_state["final_response"]
    retriever.retrieve.assert_not_called()


async def test_paper_qa_subgraph_routes_to_single_when_one_ref(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """1 enabled paper → pq_branch returns 'single' → pq_single streams
    tokens via custom and lifts final_response."""
    session_id = await _seed_session(migrated_db)
    pcid, chunk_ids = await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.0001",
        title="Solo", chunk_texts=["solo text"],
    )
    retriever = MagicMock(spec=Retriever)
    retriever.retrieve.return_value = [
        RetrievedChunk(chunk_id=chunk_ids[0], paper_content_id=pcid,
                       text="solo text", score=0.9),
    ]
    adapter = _StubAdapter([f"Answer [chunk:{chunk_ids[0]}]"])
    graph = build_paper_qa_subgraph(
        _deps(conn=migrated_db, tracer=fake_tracer,
              retriever=retriever, adapter=adapter),
    )
    state = {
        "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
        "session_id": session_id, "user_message": "tell me",
    }
    customs, final_state = await _collect(graph, state)
    token_evts = [c for c in customs if c.get("event") == "token"]
    assert len(token_evts) >= 1
    assert f"[chunk:{chunk_ids[0]}]" in final_state["final_response"]


async def test_paper_qa_subgraph_routes_to_map_when_multiple_refs(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """N>=2 enabled papers → 'map' → pq_map runs in parallel → pq_synthesize
    streams synthesizer tokens via custom."""
    session_id = await _seed_session(migrated_db)
    pcid_a, chunks_a = await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.0010",
        title="A", chunk_texts=["a text"],
    )
    pcid_b, chunks_b = await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.0011",
        title="B", chunk_texts=["b text"],
    )
    retriever = MagicMock(spec=Retriever)
    retriever.retrieve.side_effect = [
        [RetrievedChunk(chunk_id=chunks_a[0], paper_content_id=pcid_a,
                        text="a text", score=0.9)],
        [RetrievedChunk(chunk_id=chunks_b[0], paper_content_id=pcid_b,
                        text="b text", score=0.85)],
    ]
    synth_tokens = [
        f"Both [chunk:{chunks_a[0]}] ",
        f"and [chunk:{chunks_b[0]}].",
    ]
    adapter = _StubAdapter(
        tokens=synth_tokens,
        token_map={
            "A": ["a analysis"],
            "B": ["b analysis"],
            "paper_qa_synthesize/v1": synth_tokens,
        },
    )
    graph = build_paper_qa_subgraph(
        _deps(conn=migrated_db, tracer=fake_tracer,
              retriever=retriever, adapter=adapter),
    )
    state = {
        "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
        "session_id": session_id, "user_message": "compare",
    }
    customs, final_state = await _collect(graph, state)
    token_evts = [c for c in customs if c.get("event") == "token"]
    assert len(token_evts) >= 2
    body = final_state["final_response"]
    assert f"[chunk:{chunks_a[0]}]" in body
    assert f"[chunk:{chunks_b[0]}]" in body


async def test_paper_qa_subgraph_map_runs_in_parallel(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """pq_map fans out via asyncio.gather. 3 papers × 0.2 s latency must
    finish in <0.5 s, not ~0.6 s sequential."""
    import time
    session_id = await _seed_session(migrated_db)
    pcid_a, chunks_a = await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.1100",
        title="P1", chunk_texts=["c1"],
    )
    pcid_b, chunks_b = await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.1101",
        title="P2", chunk_texts=["c2"],
    )
    pcid_c, chunks_c = await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.1102",
        title="P3", chunk_texts=["c3"],
    )
    retriever = MagicMock(spec=Retriever)
    retriever.retrieve.side_effect = [
        [RetrievedChunk(chunk_id=chunks_a[0], paper_content_id=pcid_a,
                        text="c1", score=0.9)],
        [RetrievedChunk(chunk_id=chunks_b[0], paper_content_id=pcid_b,
                        text="c2", score=0.8)],
        [RetrievedChunk(chunk_id=chunks_c[0], paper_content_id=pcid_c,
                        text="c3", score=0.7)],
    ]
    adapter = _StubAdapter(
        tokens=["synth"],
        token_map={"P1": ["p1"], "P2": ["p2"], "P3": ["p3"]},
        latency=0.2,
    )
    graph = build_paper_qa_subgraph(
        _deps(conn=migrated_db, tracer=fake_tracer,
              retriever=retriever, adapter=adapter),
    )
    state = {
        "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
        "session_id": session_id, "user_message": "compare all",
    }
    t0 = time.monotonic()
    await _collect(graph, state)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, (
        f"pq_map steps appear sequential: elapsed={elapsed:.2f}s"
    )


async def test_paper_qa_subgraph_synthesize_short_circuit_when_no_chunks(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """When every paper returns 0 chunks in the map step, pq_synthesize
    short-circuits with the sentinel — no synthesizer LLM call."""
    session_id = await _seed_session(migrated_db)
    await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.2200",
        title="A", chunk_texts=["a"],
    )
    await _seed_paper_with_chunks(
        migrated_db, session_id=session_id, arxiv_id="2401.2201",
        title="B", chunk_texts=["b"],
    )
    retriever = MagicMock(spec=Retriever)
    retriever.retrieve.return_value = []
    adapter = _StubAdapter(["should not appear"])
    graph = build_paper_qa_subgraph(
        _deps(conn=migrated_db, tracer=fake_tracer,
              retriever=retriever, adapter=adapter),
    )
    state = {
        "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
        "session_id": session_id, "user_message": "anything",
    }
    customs, final_state = await _collect(graph, state)
    assert [c for c in customs if c.get("event") == "token"] == []
    assert "No relevant chunks" in final_state["final_response"]


# ---------------------------------------------------------------------------
# Outer Research dispatcher subgraph
# ---------------------------------------------------------------------------


async def test_research_dispatcher_routes_to_paper_search(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    """routing_decision.intent='paper_search' → outer dispatcher embeds the
    paper_search subgraph as a node and the ps_finalize state surfaces."""
    seq = [_msg(content="dispatched ok")]
    comp = AsyncMock(side_effect=seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp):
        graph = build_research_subgraph(
            _deps(conn=migrated_db, tracer=fake_tracer, pipeline=fake_pipeline),
        )
        state = {
            "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
            "session_id": 1, "user_message": "find papers",
            "routing_decision": _decision("paper_search"),
        }
        _, final_state = await _collect(graph, state)
    assert "dispatched ok" in final_state["final_response"]


async def test_research_dispatcher_routes_to_paper_qa(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """routing_decision.intent='paper_qa' → outer dispatcher embeds the
    paper_qa subgraph as a node; empty refs falls through to pq_empty."""
    session_id = await _seed_session(migrated_db)
    retriever = MagicMock(spec=Retriever)
    adapter = MagicMock()
    graph = build_research_subgraph(
        _deps(conn=migrated_db, tracer=fake_tracer,
              retriever=retriever, adapter=adapter),
    )
    state = {
        "run_id": fake_tracer._run_id, "branch": "",  # noqa: SLF001
        "session_id": session_id, "user_message": "anything",
        "routing_decision": _decision("paper_qa"),
    }
    _, final_state = await _collect(graph, state)
    assert "No references are enabled" in final_state["final_response"]


# Compatibility surface used by other tests / chat layer; silence ruff.
_ = SemanticScholarToolHit
