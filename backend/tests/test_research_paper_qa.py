"""Research Agent paper_qa streaming tests (SRS v2.3, FR-03, I-8 #3).

v2.10 update: dense-RAG map-reduce path replaced with agentic hierarchical
pipeline. Tests that exercised ``_paper_qa_map_one`` / ``_paper_qa_synthesize_stream``
/ ``_paper_qa_single_*`` / ``_paper_qa_map_reduce`` have been removed.  The
legacy ``paper_qa_stream`` façade is retained for backward-compat and still
tested through its single-entry contract. New tests cover ``paper_qa_finalize``
(the finalizer streaming helper) which replaces the old synthesizer.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.research import FinalOnlyMessage, paper_qa_stream
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


async def _make_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


class _StubAdapter:
    """LlmAdapter stub whose ``stream`` yields a pre-canned token list.

    The ``slot`` key in ``token_map`` lets specific prompt slots return
    different tokens. Falls back to ``tokens`` when no key matches.
    """

    def __init__(
        self,
        tokens: list[str],
        *,
        token_map: dict[str, list[str]] | None = None,
    ) -> None:
        self._tokens = tokens
        self._token_map = token_map or {}
        self.last_variables: dict[str, Any] | None = None
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
        self.last_variables = variables
        self.calls.append({"slot": slot, "variables": dict(variables)})

        tokens: list[str] = (
            list(self._token_map[slot]) if slot in self._token_map else list(self._tokens)
        )

        async def _gen() -> AsyncIterator[str]:
            for t in tokens:
                yield t

        return _gen()


# ---------------------------------------------------------------------------
# Legacy paper_qa_stream — empty-session fast-path
# ---------------------------------------------------------------------------

async def test_paper_qa_no_enabled_papers_short_circuits(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """If the session has no enabled papers, yield a FinalOnlyMessage and stop."""
    session_id = await _make_session(migrated_db)
    adapter = _StubAdapter(["should not stream"])

    state = {
        "run_id": fake_tracer._run_id,  # noqa: SLF001
        "branch": "", "session_id": session_id,
        "user_message": "anything",
    }
    out: list[str | FinalOnlyMessage] = []
    async for item in paper_qa_stream(
        state, adapter=adapter, tracer=fake_tracer,
        model="m", conn=migrated_db,
    ):
        out.append(item)
    assert len(out) == 1
    assert isinstance(out[0], FinalOnlyMessage)
    assert "No references are enabled" in out[0].content
    assert adapter.last_variables is None


# ---------------------------------------------------------------------------
# v2.10-4: paper_qa_finalize — the new streaming finalizer
# ---------------------------------------------------------------------------


async def test_paper_qa_finalizer_streams_synthesis_with_chunk_markers_preserved(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """The finalizer prompt embeds raw chunk text + rationale per paper;
    its streaming output preserves ``[chunk:N]`` markers for the Citation
    Canvas."""
    from paperhub.agents.paper_qa_subagent import PerPaperPicks, PickedChunk
    from paperhub.agents.research import paper_qa_finalize

    picks = [
        PerPaperPicks(
            paper_content_id=15,
            title="MolmoAct",
            picked_chunks=[
                PickedChunk(
                    chunk_id=101,
                    text="We compute action tokens via Q-former.",
                    section="Method",
                ),
                PickedChunk(
                    chunk_id=102,
                    text="Loss is cross-entropy on tokenized actions.",
                    section="Method",
                ),
            ],
            rationale="Method centers on action tokenization.",
        ),
        PerPaperPicks(
            paper_content_id=16,
            title="X-VLA",
            picked_chunks=[
                PickedChunk(
                    chunk_id=203,
                    text="Soft prompts learned per embodiment.",
                    section="Architecture",
                ),
            ],
            rationale="Method centers on soft-prompt heterogeneity.",
        ),
    ]

    # The stub returns tokens that preserve the chunk markers from the prompt.
    stub_tokens = (
        "Both papers tokenize actions [chunk:101] but X-VLA adds soft "
        "prompts [chunk:203]. Loss is CE [chunk:102]."
    )
    adapter = _StubAdapter(
        tokens=[stub_tokens],
        token_map={"paper_qa_synthesize/v2": [stub_tokens]},
    )

    tokens: list[str] = []
    async for tok in paper_qa_finalize(
        per_paper_picks=picks,
        user_message="compare the methods",
        adapter=adapter,
        tracer=fake_tracer,
        model="stub",
        state={"run_id": fake_tracer._run_id, "history": None},  # type: ignore[arg-type]  # noqa: SLF001
    ):
        tokens.append(tok)

    out = "".join(tokens)
    assert "[chunk:101]" in out
    assert "[chunk:203]" in out


async def test_paper_qa_finalizer_uses_synthesize_v2_slot(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """paper_qa_finalize must call adapter.stream with slot='paper_qa_synthesize/v2'."""
    from paperhub.agents.paper_qa_subagent import PerPaperPicks, PickedChunk
    from paperhub.agents.research import paper_qa_finalize

    picks = [
        PerPaperPicks(
            paper_content_id=1,
            title="SomePaper",
            picked_chunks=[PickedChunk(chunk_id=10, text="relevant text.", section="Intro")],
            rationale="Introductory content.",
        ),
    ]
    adapter = _StubAdapter(tokens=["answer"])
    tokens: list[str] = []
    async for tok in paper_qa_finalize(
        per_paper_picks=picks,
        user_message="what is this about?",
        adapter=adapter,
        tracer=fake_tracer,
        model="stub",
        state={"run_id": fake_tracer._run_id, "history": None},  # type: ignore[arg-type]  # noqa: SLF001
    ):
        tokens.append(tok)
    assert any(c["slot"] == "paper_qa_synthesize/v2" for c in adapter.calls), (
        f"Expected paper_qa_synthesize/v2 slot call; got: {[c['slot'] for c in adapter.calls]}"
    )


# ---------------------------------------------------------------------------
# v2.16 FR-10: paper_qa_finalize recall injection
# ---------------------------------------------------------------------------


async def test_paper_qa_finalizer_passes_memory_context_to_prompt(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """When memory_context is non-empty, the finalizer forwards it in the
    prompt variables so the model sees the recalled facts."""
    from paperhub.agents.paper_qa_subagent import PerPaperPicks, PickedChunk
    from paperhub.agents.research import paper_qa_finalize

    picks = [
        PerPaperPicks(
            paper_content_id=1,
            title="SomePaper",
            picked_chunks=[PickedChunk(chunk_id=10, text="relevant text.", section="Intro")],
            rationale="Introductory content.",
        ),
    ]
    adapter = _StubAdapter(tokens=["answer with context"])
    tokens: list[str] = []
    async for tok in paper_qa_finalize(
        per_paper_picks=picks,
        user_message="what is this about?",
        adapter=adapter,
        tracer=fake_tracer,
        model="stub",
        state={"run_id": fake_tracer._run_id, "history": None},  # type: ignore[arg-type]  # noqa: SLF001
        memory_context="Relevant remembered facts (use if helpful, ignore if not):\n- (global) answer in Traditional Chinese",
    ):
        tokens.append(tok)

    # The memory_context should appear in the prompt variables passed to adapter.stream.
    synth_calls = [c for c in adapter.calls if c["slot"] == "paper_qa_synthesize/v2"]
    assert synth_calls, "Expected paper_qa_synthesize/v2 slot call"
    assert "Traditional Chinese" in synth_calls[0]["variables"].get("memory_context", "")


async def test_paper_qa_finalizer_empty_memory_context_renders_harmlessly(
    migrated_db: aiosqlite.Connection,
    fake_tracer: Tracer,
) -> None:
    """When memory_context is empty (default), the prompt still renders without
    a placeholder visible in the output."""
    from paperhub.agents.paper_qa_subagent import PerPaperPicks, PickedChunk
    from paperhub.agents.research import paper_qa_finalize
    from paperhub.llm.prompts.registry import PromptRegistry

    picks = [
        PerPaperPicks(
            paper_content_id=1,
            title="SomePaper",
            picked_chunks=[PickedChunk(chunk_id=10, text="relevant text.", section="Intro")],
            rationale="Introductory content.",
        ),
    ]
    adapter = _StubAdapter(tokens=["answer"])
    tokens: list[str] = []
    async for tok in paper_qa_finalize(
        per_paper_picks=picks,
        user_message="what is this about?",
        adapter=adapter,
        tracer=fake_tracer,
        model="stub",
        state={"run_id": fake_tracer._run_id, "history": None},  # type: ignore[arg-type]  # noqa: SLF001
        # memory_context defaults to ""
    ):
        tokens.append(tok)

    synth_calls = [c for c in adapter.calls if c["slot"] == "paper_qa_synthesize/v2"]
    assert synth_calls
    # Empty memory_context renders without a literal placeholder in the prompt.
    slot = PromptRegistry().get("paper_qa_synthesize/v2")
    rendered = slot.user_template.format(**synth_calls[0]["variables"])
    assert "{memory_context}" not in rendered


# ---------------------------------------------------------------------------
# v2.29 FR-13: slide-aware QA — slide context prepended to subagent query
# ---------------------------------------------------------------------------


async def test_slide_context_reaches_subagent_query(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, monkeypatch,
) -> None:
    from unittest.mock import MagicMock

    from paperhub.agents import research_graph as rg
    from paperhub.agents.paper_qa_subagent import PerPaperPicks
    from paperhub.mcp.registry import MCPRegistry
    from paperhub.pipelines.paper_pipeline import PaperPipeline

    session_id = await _make_session(migrated_db)
    captured: dict[str, str] = {}

    async def _fake_resolve(*_a, **_k):
        return [(15, "FASTerVQ")]

    async def _fake_subagent(*, user_message: str, **_k):
        captured["user_message"] = user_message
        return PerPaperPicks(paper_content_id=15, title="FASTerVQ",
                             picked_chunks=[], rationale="")

    monkeypatch.setattr(rg, "_resolve_enabled_papers", _fake_resolve)
    monkeypatch.setattr(rg, "run_paper_qa_subagent", _fake_subagent)

    deps = rg.ResearchDeps(
        adapter=_StubAdapter(["ok"]),  # type: ignore[arg-type]
        tracer=fake_tracer,
        paper_qa_model="m",
        conn=migrated_db,
        pipeline=MagicMock(spec=PaperPipeline),
        mcp_registry=MagicMock(spec=MCPRegistry),
    )
    graph = rg.build_paper_qa_subgraph(deps)
    state = {"run_id": fake_tracer._run_id, "branch": "", "session_id": session_id,  # noqa: SLF001
             "user_message": "explain this graph",
             "effective_query": "explain the graph on the current slide",
             "slide_context": "Active slide (page 5) title: FASTerVQ Architecture"}
    async for _ in graph.astream(state, stream_mode=["values"]):
        pass
    assert captured["user_message"].startswith(
        "Active slide (page 5) title: FASTerVQ Architecture")
    assert "explain the graph on the current slide" in captured["user_message"]
