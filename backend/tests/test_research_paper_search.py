"""Research Agent paper_search loop tests (SRS v2.4, FR-07).

v2.4 contract: the agent is read-only. Each final assistant message ends
with a ``json:candidates`` fenced block. Up to 2 picks may carry
``finalize: true``; the chat layer auto-attaches those — NOT the agent.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from paperhub.agents.research import (
    FinalOnlyMessage,
    SearchResultsYield,
    ToolStepYield,
    _extract_candidates,
    paper_search,
)
from paperhub.agents.research_tools import (
    ArxivHit,
    LibraryHit,
    SemanticScholarToolHit,
)
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _collect(gen: Any) -> tuple[str, list[Any]]:
    """Consume the paper_search async generator; return (final_content, all_items)."""
    items: list[Any] = []
    async for item in gen:
        items.append(item)
    final_msg = next(i for i in items if isinstance(i, FinalOnlyMessage))
    return final_msg.content, items


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


def _candidates_block(picks: list[dict[str, Any]]) -> str:
    return "```json:candidates\n" + json.dumps(picks) + "\n```"


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
        out, items = await _collect(paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="gemini/gemini-2.5-flash",
            conn=migrated_db, pipeline=fake_pipeline,
        ))
    assert "?" in out
    assert comp.await_count == 1
    # Streaming contract: at least the plan step is yielded as ToolStepYield.
    tool_steps = [i for i in items if isinstance(i, ToolStepYield)]
    assert len(tool_steps) >= 1
    # No SearchResultsYield for clarification turns.
    assert not any(isinstance(i, SearchResultsYield) for i in items)


# ---------- Case 2: library hit → shortlist (no external search) ----------
async def test_library_hit_shortlists_without_external_search(
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
    block = _candidates_block(
        [
            {
                "paper_id": f"library:{seed_library}",
                "reason": "the original transformer paper",
                "finalize": True,
            },
        ],
    )
    seq = [
        _msg(tool_calls=[
            _tool_call("c1", "search_library",
                       {"query": "transformer", "max_results": 8}),
        ]),
        _msg(
            content=(
                "I found 'Attention Is All You Need' in your library.\n\n" + block
            ),
        ),
    ]
    comp = _async_completion_mock(seq)
    ss_mock = AsyncMock(return_value=[])
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=lib_hits)), \
         patch("paperhub.agents.research.search_semantic_scholar_dispatch",
               new=ss_mock):
        out, items = await _collect(paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        ))
    # Final content has the prose but NOT the fenced block.
    assert "Attention Is All You Need" in out
    assert "json:candidates" not in out
    # The shortlist must be surfaced as a SearchResultsYield.
    yields = [i for i in items if isinstance(i, SearchResultsYield)]
    assert len(yields) == 1
    cands = yields[0].candidates
    assert len(cands) == 1
    assert cands[0].paper_id == f"library:{seed_library}"
    assert cands[0].finalize is True
    # I-8 #9: library-first preference — no external search called
    ss_mock.assert_not_called()


# ---------- Case 3: library miss → search_semantic_scholar → shortlist ----------
async def test_library_miss_falls_through_to_semantic_scholar(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    state = {
        "run_id": 3, "branch": "", "session_id": 1,
        "user_message": "find me mixture-of-experts routing papers",
    }
    ss_hits = [
        SemanticScholarToolHit(
            paper_id="arxiv:2403.00001",
            title="MoE Routing X",
            abstract="...",
            year=2024,
            authors=["A"],
            arxiv_id="2403.00001",
            has_open_pdf=True,
        ),
    ]
    block = _candidates_block(
        [
            {
                "paper_id": "arxiv:2403.00001",
                "reason": "top MoE routing hit",
                "finalize": True,
            },
        ],
    )
    seq = [
        _msg(tool_calls=[
            _tool_call("c1", "search_library",
                       {"query": "mixture of experts routing"}),
        ]),
        _msg(tool_calls=[
            _tool_call("c2", "search_semantic_scholar",
                       {"query": "mixture of experts routing"}),
        ]),
        _msg(content="Found 'MoE Routing X'.\n\n" + block),
    ]
    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=[])), \
         patch("paperhub.agents.research.search_semantic_scholar_dispatch",
               new=AsyncMock(return_value=ss_hits)):
        out, items = await _collect(paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        ))
    assert "MoE Routing X" in out
    yields = [i for i in items if isinstance(i, SearchResultsYield)]
    assert len(yields) == 1
    assert yields[0].candidates[0].paper_id == "arxiv:2403.00001"


# ---------- Case 4: external search refinement loop (N=2 calls, both succeed) ----------
async def test_external_search_refinement_within_cap(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    state = {
        "run_id": 4, "branch": "", "session_id": 1,
        "user_message": "find recent paper_qa work",
    }
    ss_hit = SemanticScholarToolHit(
        paper_id="arxiv:2404.00002",
        title="Paper QA", abstract="...", year=2024, authors=[],
        arxiv_id="2404.00002", has_open_pdf=False,
    )
    block = _candidates_block(
        [{"paper_id": "arxiv:2404.00002", "reason": "best refined hit"}],
    )
    seq = [
        _msg(tool_calls=[
            _tool_call("c1", "search_library", {"query": "paper qa"}),
        ]),
        _msg(tool_calls=[
            _tool_call("c2", "search_semantic_scholar", {"query": "paper QA"}),
        ]),
        # First external call weak — refine
        _msg(tool_calls=[
            _tool_call("c3", "search_semantic_scholar",
                       {"query": "scientific paper question answering 2024"}),
        ]),
        _msg(content="Refined hit:\n\n" + block),
    ]
    comp = _async_completion_mock(seq)
    ss_results: list[list[SemanticScholarToolHit]] = [
        [],
        [ss_hit],
    ]
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_library_dispatch",
               new=AsyncMock(return_value=[])), \
         patch("paperhub.agents.research.search_semantic_scholar_dispatch",
               new=AsyncMock(side_effect=ss_results)):
        out, items = await _collect(paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        ))
    assert "Paper QA" in out or "Refined" in out
    yields = [i for i in items if isinstance(i, SearchResultsYield)]
    assert len(yields) == 1
    assert yields[0].candidates[0].paper_id == "arxiv:2404.00002"


# ---------- Case 5: external search cap (N=3) enforced — 4th call returns cap error ----------
async def test_external_search_cap_enforced_at_three(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
    fake_pipeline: MagicMock,
) -> None:
    """4th search_semantic_scholar must NOT actually invoke the dispatcher;
    tool result returns {error: external_search_call_cap_reached}."""
    state = {
        "run_id": 5, "branch": "", "session_id": 1,
        "user_message": "keep refining",
    }
    call4 = _tool_call("c4", "search_semantic_scholar", {"query": "v4"})
    seq = [
        _msg(tool_calls=[_tool_call("c1", "search_semantic_scholar", {"query": "v1"})]),
        _msg(tool_calls=[_tool_call("c2", "search_semantic_scholar", {"query": "v2"})]),
        _msg(tool_calls=[_tool_call("c3", "search_semantic_scholar", {"query": "v3"})]),
        _msg(tool_calls=[call4]),  # 4th — must be capped
        _msg(content="I've reached the search cap."),
    ]
    ss_calls = 0

    async def fake_ss(**_: Any) -> list[SemanticScholarToolHit]:
        nonlocal ss_calls
        ss_calls += 1
        return []

    comp = _async_completion_mock(seq)
    with patch("paperhub.agents.research.litellm.acompletion", new=comp), \
         patch("paperhub.agents.research.search_semantic_scholar_dispatch",
               side_effect=fake_ss):
        await _collect(paper_search(
            state, adapter=None, tracer=fake_tracer,
            model="m", conn=migrated_db, pipeline=fake_pipeline,
        ))
    # Dispatcher invoked only 3 times — 4th was capped before dispatch.
    assert ss_calls == 3


# ---------------------------------------------------------------------------
# _extract_candidates unit tests
# ---------------------------------------------------------------------------


def test_extract_candidates_parses_finalize_flag() -> None:
    recent = {
        "library:42": {
            "title": "Foundational MoE",
            "authors": [],
            "year": 2017,
            "abstract": "abs",
            "arxiv_id": None,
            "has_open_pdf": False,
        },
        "ss:abcd": {
            "title": "Mamba follow-up",
            "authors": ["X"],
            "year": 2024,
            "abstract": "abs",
            "arxiv_id": None,
            "has_open_pdf": True,
        },
    }
    text = (
        "Here are picks.\n\n"
        "```json:candidates\n"
        + json.dumps(
            [
                {"paper_id": "library:42", "reason": "r1", "finalize": True},
                {"paper_id": "ss:abcd", "reason": "r2", "finalize": True},
            ],
        )
        + "\n```\n"
    )
    cleaned, cands = _extract_candidates(text, recent)
    assert "json:candidates" not in cleaned
    assert len(cands) == 2
    assert all(c.finalize for c in cands)
    assert cands[0].paper_id == "library:42"
    assert cands[1].paper_id == "ss:abcd"


def test_extract_candidates_strips_json_block_from_final_text() -> None:
    recent = {
        "arxiv:1234.5678": {
            "title": "T",
            "authors": [],
            "year": 2024,
            "abstract": "",
            "arxiv_id": "1234.5678",
            "has_open_pdf": False,
        },
    }
    text = (
        "prose summary.\n\n"
        "```json:candidates\n"
        + json.dumps([{"paper_id": "arxiv:1234.5678", "reason": "r"}])
        + "\n```\n"
    )
    cleaned, cands = _extract_candidates(text, recent)
    assert "```" not in cleaned
    assert "json:candidates" not in cleaned
    assert "prose summary." in cleaned
    assert len(cands) == 1


def test_extract_candidates_tolerant_when_block_missing() -> None:
    """Agent forgot the block → empty list, original text preserved."""
    text = "Just a prose answer with no fenced block."
    cleaned, cands = _extract_candidates(text, {})
    assert cleaned == text
    assert cands == []


def test_extract_candidates_tolerant_when_block_malformed_json() -> None:
    """Agent emitted the fence but the JSON inside is syntactically broken —
    strip the block so it doesn't leak to the user, return empty candidates."""
    text = (
        "Here's a shortlist:\n"
        "```json:candidates\n"
        "[{paper_id: library:1, reason: broken — no quotes]\n"
        "```\n"
    )
    cleaned, cands = _extract_candidates(text, {"library:1": {"title": "X"}})
    assert "```json:candidates" not in cleaned, "malformed block must still be stripped"
    assert "Here's a shortlist:" in cleaned
    assert cands == []


def test_extract_candidates_drops_unknown_paper_ids() -> None:
    """The agent occasionally hallucinates paper_ids it didn't search for —
    drop them defensively."""
    recent = {
        "library:1": {
            "title": "Real",
            "authors": [],
            "year": 2024,
            "abstract": "",
            "arxiv_id": None,
            "has_open_pdf": False,
        },
    }
    text = (
        "p\n```json:candidates\n"
        + json.dumps(
            [
                {"paper_id": "library:1", "reason": "real"},
                {"paper_id": "library:999", "reason": "halluc"},
            ],
        )
        + "\n```\n"
    )
    _, cands = _extract_candidates(text, recent)
    assert {c.paper_id for c in cands} == {"library:1"}


# Compatibility: keep importable ArxivHit (used elsewhere); silence ruff
_ = ArxivHit  # noqa: F841 — kept for backward-compat surface
