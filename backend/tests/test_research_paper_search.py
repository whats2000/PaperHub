"""Research Agent paper_search loop tests (SRS v2.3, FR-07)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from paperhub.agents.research import paper_search
from paperhub.agents.research_tools import AddResult, ArxivHit, LibraryHit
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


def _msg(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a fake LiteLLM response object."""
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


def _async_completion_mock(responses: list[dict[str, Any]]) -> AsyncMock:
    """Create an AsyncMock for litellm.acompletion that returns each response
    in sequence on successive awaits."""
    return AsyncMock(side_effect=responses)


# ---------- Case 1: vague prompt → clarifying question, zero tool calls ----------
async def test_vague_prompt_emits_clarifying_question(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    state = {
        "run_id": 1, "branch": "", "session_id": 1,
        "user_message": "find me good ML papers",
    }
    seq = [
        _msg(
            content="What problem are you trying to solve — routing, "
            "training stability, or something else?",
        ),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp):
        out = await paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="gemini/gemini-2.5-flash",
            conn=migrated_db, pipeline=fake_pipeline,
        )
    assert "?" in out
    assert comp.await_count == 1


# ---------- Case 2: clear prompt → library hit → add → respond (no arxiv) ----------
async def test_library_hit_skips_arxiv(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
    seed_library: int,
) -> None:
    """seed_library inserts a paper_content row the agent can hit."""
    state = {
        "run_id": 2, "branch": "", "session_id": 1,
        "user_message": "I want the original transformer paper",
    }
    lib_hits = [
        LibraryHit(
            paper_content_id=seed_library,
            arxiv_id="1706.03762",
            title="Attention Is All You Need",
            abstract="...",
            year=2017,
        ),
    ]
    seq = [
        _msg(tool_calls=[
            _tool_call("c1", "search_library",
                       {"query": "transformer", "max_results": 8}),
        ]),
        _msg(tool_calls=[
            _tool_call("c2", "add_paper_to_session",
                       {"paper_id": f"library:{seed_library}",
                        "reason": "the original transformer paper"}),
        ]),
        _msg(content="Added 'Attention Is All You Need' from your library."),
    ]
    comp = _async_completion_mock(seq)
    add_mock = AsyncMock(return_value=AddResult(
        seed_library, 99, cache_hit=True, title="Attention Is All You Need",
    ))
    arxiv_mock = AsyncMock(return_value=[])
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=lib_hits)), \
         patch("paperhub.agents.research.search_arxiv_dispatch", new=arxiv_mock), \
         patch("paperhub.agents.research.add_paper_to_session_dispatch",
               new=add_mock):
        out = await paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        )
    assert "Attention Is All You Need" in out
    add_mock.assert_awaited_once()
    # I-8 #9: library-first preference — no search_arxiv ever called
    arxiv_mock.assert_not_called()


# ---------- Case 3: library miss → arxiv → add → respond ----------
async def test_library_miss_falls_through_to_arxiv(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    state = {
        "run_id": 3, "branch": "", "session_id": 1,
        "user_message": "find me mixture-of-experts routing papers",
    }
    arx_hits = [
        ArxivHit(arxiv_id="2403.00001", title="MoE Routing X",
                 abstract="...", year=2024, authors=["A"]),
    ]
    seq = [
        _msg(tool_calls=[
            _tool_call("c1", "search_library",
                       {"query": "mixture of experts routing"}),
        ]),
        _msg(tool_calls=[
            _tool_call("c2", "search_arxiv",
                       {"query": "mixture of experts routing"}),
        ]),
        _msg(tool_calls=[
            _tool_call("c3", "add_paper_to_session",
                       {"paper_id": "arxiv:2403.00001",
                        "reason": "top MoE routing hit"}),
        ]),
        _msg(content="Added MoE Routing X from arXiv."),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=[])), \
         patch("paperhub.agents.research.search_arxiv_dispatch",
               new=AsyncMock(return_value=arx_hits)), \
         patch("paperhub.agents.research.add_paper_to_session_dispatch",
               new=AsyncMock(return_value=AddResult(
                   7, 12, cache_hit=False, title="MoE Routing X"))):
        out = await paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        )
    assert "MoE Routing X" in out


# ---------- Case 4: arxiv refinement loop (N=2 calls, both succeed) ----------
async def test_arxiv_refinement_within_cap(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    state = {
        "run_id": 4, "branch": "", "session_id": 1,
        "user_message": "find recent paper_qa work",
    }
    seq = [
        _msg(tool_calls=[
            _tool_call("c1", "search_library", {"query": "paper qa"}),
        ]),
        _msg(tool_calls=[
            _tool_call("c2", "search_arxiv", {"query": "paper QA"}),
        ]),
        # First arxiv call weak — refine
        _msg(tool_calls=[
            _tool_call("c3", "search_arxiv",
                       {"query": "scientific paper question answering 2024"}),
        ]),
        _msg(tool_calls=[
            _tool_call("c4", "add_paper_to_session",
                       {"paper_id": "arxiv:2404.00002",
                        "reason": "best refined hit"}),
        ]),
        _msg(content="Added one paper after refining the query."),
    ]
    comp = _async_completion_mock(seq)
    arxiv_results: list[list[ArxivHit]] = [
        [],
        [ArxivHit("2404.00002", "Paper QA", "...", 2024, [])],
    ]
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=[])), \
         patch("paperhub.agents.research.search_arxiv_dispatch",
               new=AsyncMock(side_effect=arxiv_results)), \
         patch("paperhub.agents.research.add_paper_to_session_dispatch",
               new=AsyncMock(return_value=AddResult(
                   8, 13, False, "Paper QA"))):
        out = await paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        )
    assert "refining" in out.lower() or "Paper QA" in out


# ---------- Case 5: arxiv cap (N=3) enforced — 4th call returns cap error ----------
async def test_arxiv_cap_enforced_at_three(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    """4th search_arxiv must NOT actually invoke the dispatcher;
    tool result returns {error: arxiv_call_cap_reached}."""
    state = {
        "run_id": 5, "branch": "", "session_id": 1,
        "user_message": "keep refining",
    }
    call4 = _tool_call("c4", "search_arxiv", {"query": "v4"})
    seq = [
        _msg(tool_calls=[_tool_call("c1", "search_arxiv", {"query": "v1"})]),
        _msg(tool_calls=[_tool_call("c2", "search_arxiv", {"query": "v2"})]),
        _msg(tool_calls=[_tool_call("c3", "search_arxiv", {"query": "v3"})]),
        _msg(tool_calls=[call4]),  # 4th — must be capped
        _msg(content="I've reached the search cap."),
    ]
    arx_dispatcher_calls = 0

    async def fake_arxiv(**_: Any) -> list[ArxivHit]:
        nonlocal arx_dispatcher_calls
        arx_dispatcher_calls += 1
        return []

    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_arxiv_dispatch",
               side_effect=fake_arxiv):
        await paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        )
    # Dispatcher invoked only 3 times — 4th was capped before dispatch.
    assert arx_dispatcher_calls == 3
