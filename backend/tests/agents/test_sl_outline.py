"""Tests for the F6.1 sl_outline multi-round orchestrator loop.

Tests are fully deterministic — the LLM adapter and gather_fn are stubbed.
No real DB chunk resolution is needed (grounding comes from gather_fn bundles,
not SQL sections).
"""
import inspect
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

import paperhub.agents.report_graph as rg  # noqa: F401 (used by wiring test)
from paperhub.agents.sl_outline import run_sl_outline
from paperhub.db.migrate import apply_schema
from paperhub.models.slide_domain import (
    ContextRequest,
    DeckOutline,
    DeckOutlineDraft,
    OutlineResult,
    OutlineSlideDraft,
    PaperContextBundle,
    RoundAction,
    SectionExcerpt,
    SeedFigure,
    SeedPaper,
)
from paperhub.tracing.tracer import Tracer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(tmp_path / "t.db")) as c:
        await apply_schema(c)
        # Insert chat_sessions + runs rows so Tracer can write tool_calls
        await c.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await c.execute("INSERT INTO runs (session_id) VALUES (1)")
        await c.commit()
        yield c


def _tracer(c: aiosqlite.Connection) -> Tracer:
    return Tracer(c, run_id=1, branch="")


def _seeds() -> list[SeedPaper]:
    return [
        SeedPaper(
            paper_id=73,
            title="Paper A",
            abstract="Abstract of paper A",
            is_survey=False,
            sections=["Intro", "Method", "Results", "Conclusion"],
            figures=[SeedFigure(key="p0-fig-001", caption="Architecture diagram")],
        ),
        SeedPaper(
            paper_id=74,
            title="Paper B",
            abstract="Abstract of paper B",
            is_survey=False,
            sections=["Intro", "Approach", "Experiments"],
            figures=[SeedFigure(key="p1-fig-001", caption="Results chart")],
        ),
    ]


def _bundle(paper_id: int, aim: str, chunk_ids: list[int]) -> PaperContextBundle:
    return PaperContextBundle(
        paper_id=paper_id,
        paper_idx=0,
        title=f"Paper {paper_id}",
        authors=["Author"],
        year=2025,
        narrative_summary=f"Summary for aim={aim!r}",
        key_figures=[],
        key_equations=[],
        section_excerpts=[
            SectionExcerpt(section_name="Method", text=f"Evidence text for {aim}")
        ],
        paper_newcommands=[],
        read_chunk_ids=chunk_ids,
    )


# ---------------------------------------------------------------------------
# Scripted adapter
# ---------------------------------------------------------------------------

class _ScriptedAdapter:
    """Returns a scripted sequence of RoundActions; records call variables."""

    def __init__(self, script: list[RoundAction]) -> None:
        self._script = list(script)
        self._call_idx = 0
        self.variables_log: list[dict[str, Any]] = []

    async def structured(
        self, *, slot: str, variables: dict[str, Any], response_model: type, model: str, **kw: Any
    ) -> Any:
        assert slot == "slides_outline/v1", f"unexpected slot {slot!r}"
        assert response_model is RoundAction, f"unexpected response_model {response_model}"
        self.variables_log.append(dict(variables))
        idx = self._call_idx
        self._call_idx += 1
        if idx < len(self._script):
            return self._script[idx]
        # If exhausted — return a finalize with whatever we have
        return RoundAction(
            action="finalize",
            narrative_pattern="synthesis",
            outline=DeckOutlineDraft(
                talk_title="Fallback",
                audience_intent="walk through references",
                narrative_arc="intro -> synthesis",
                slides=[OutlineSlideDraft(goal="Title", key_message="")],
            ),
        )


# ---------------------------------------------------------------------------
# Stub gather_fn
# ---------------------------------------------------------------------------

class _GatherTracker:
    """Records all gather_fn calls."""

    def __init__(self, bundles: dict[tuple[str, int], PaperContextBundle]) -> None:
        self._bundles = bundles
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, aim: str, paper_id: int) -> PaperContextBundle:
        self.calls.append((aim, paper_id))
        key = (aim, paper_id)
        if key in self._bundles:
            return self._bundles[key]
        return _bundle(paper_id, aim, [])


# ---------------------------------------------------------------------------
# Test 1: dispatch then finalize
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_then_finalize(conn: aiosqlite.Connection) -> None:
    """Round 1 dispatches two aims; round 2 finalizes with cites_aims pointing
    at aim 'A'.  Asserts gather_fn called correctly, grounding_chunk_ids correct,
    support_excerpts non-empty, narrative_pattern propagated."""
    chunk_ids_A = [101, 102]
    chunk_ids_B = [201]
    gather_tracker = _GatherTracker({
        ("A", 73): _bundle(73, "A", chunk_ids_A),
        ("B", 74): _bundle(74, "B", chunk_ids_B),
    })

    finalize_action = RoundAction(
        action="finalize",
        narrative_pattern="comparison",
        outline=DeckOutlineDraft(
            talk_title="Comparing A and B",
            narrative_pattern="comparison",
            audience_intent="compare the two papers",
            narrative_arc="problem -> A -> B -> synthesis",
            slides=[
                OutlineSlideDraft(
                    goal="Title slide",
                    key_message="Comparing Paper A and B",
                    content_form="title",
                    cites_aims=[],
                ),
                OutlineSlideDraft(
                    goal="Paper A Method",
                    key_message="Key mechanism",
                    content_form="comparison_table",
                    paper_id=73,
                    cites_aims=["A"],
                    speaker_note_hint="Explain how the method works and why it was designed this way.",
                ),
                OutlineSlideDraft(
                    goal="Synthesis",
                    key_message="Combined takeaway",
                    content_form="synthesis",
                    cites_aims=["A", "B"],
                ),
            ],
        ),
    )

    script = [
        RoundAction(
            action="dispatch",
            narrative_pattern="comparison",
            requests=[
                ContextRequest(aim="A", paper_id=73),
                ContextRequest(aim="B", paper_id=74),
            ],
        ),
        finalize_action,
    ]
    adapter = _ScriptedAdapter(script)
    tracer = _tracer(conn)

    result = await run_sl_outline(
        seeds=_seeds(),
        task_description="Compare these papers",
        response_language="English",
        target_slides=10,
        adapter=adapter,
        tracer=tracer,
        model="test-model",
        gather_fn=gather_tracker,
        max_rounds=4,
    )

    # gather_fn called once per aim in the dispatch
    assert set(gather_tracker.calls) == {("A", 73), ("B", 74)}

    assert isinstance(result, OutlineResult)
    assert result.rounds_used == 2

    outline = result.outline
    assert isinstance(outline, DeckOutline)
    assert outline.narrative_pattern == "comparison"
    assert len(outline.slides) == 3

    # Title slide — no cites_aims, so no grounding
    title_slide = outline.slides[0]
    assert title_slide.grounding_chunk_ids == []

    # Paper A slide cites_aims=["A"] -> should ground to chunk_ids_A
    method_slide = outline.slides[1]
    assert method_slide.grounding_chunk_ids == sorted(set(chunk_ids_A))
    assert method_slide.support_excerpts  # non-empty
    # speaker_note_hint must be carried from draft -> resolved slide
    assert method_slide.speaker_note_hint == "Explain how the method works and why it was designed this way."

    # Synthesis slide cites both A and B
    synth_slide = outline.slides[2]
    assert synth_slide.grounding_chunk_ids == sorted(set(chunk_ids_A + chunk_ids_B))


# ---------------------------------------------------------------------------
# Test 2: forced finalize at budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forced_finalize_at_budget(conn: aiosqlite.Connection) -> None:
    """An adapter that always returns dispatch must not loop forever.
    After max_rounds the loop falls back to a minimal outline and returns."""
    # Always dispatch — never finalize
    always_dispatch = RoundAction(
        action="dispatch",
        narrative_pattern="synthesis",
        requests=[ContextRequest(aim="X", paper_id=73)],
    )
    gather_tracker = _GatherTracker({("X", 73): _bundle(73, "X", [999])})

    # Script: 10 dispatches (way more than max_rounds=3)
    script = [always_dispatch] * 10
    adapter = _ScriptedAdapter(script)
    tracer = _tracer(conn)

    result = await run_sl_outline(
        seeds=_seeds(),
        task_description="Always dispatch",
        response_language="English",
        target_slides=8,
        adapter=adapter,
        tracer=tracer,
        model="test-model",
        gather_fn=gather_tracker,
        max_rounds=3,
    )

    assert isinstance(result, OutlineResult)
    assert result.rounds_used == 3  # capped at max_rounds
    assert isinstance(result.outline, DeckOutline)
    assert len(result.outline.slides) >= 1  # minimal outline synthesized


# ---------------------------------------------------------------------------
# Test 3: clamp — bad paper_id, figure_key, ungathered aim recorded in dropped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clamp_bad_paper_figure_aim(conn: aiosqlite.Connection) -> None:
    """Finalized slide with paper_id not in seeds, figure_key not in seeds,
    and a cites_aims entry that was never gathered — all should be clamped and
    recorded in the trace's 'dropped' field."""
    finalize_action = RoundAction(
        action="finalize",
        narrative_pattern="synthesis",
        outline=DeckOutlineDraft(
            talk_title="Clamping test",
            narrative_pattern="synthesis",
            audience_intent="test",
            narrative_arc="test arc",
            slides=[
                OutlineSlideDraft(
                    goal="Bad slide",
                    key_message="test",
                    content_form="bullets",
                    paper_id=999,          # NOT in seeds
                    figure_key="bad-key",  # NOT in seeds
                    cites_aims=["NEVER_GATHERED"],  # never dispatched
                ),
            ],
        ),
    )
    adapter = _ScriptedAdapter([finalize_action])
    gather_tracker = _GatherTracker({})
    tracer = _tracer(conn)

    result = await run_sl_outline(
        seeds=_seeds(),
        task_description="Clamp test",
        response_language="English",
        target_slides=8,
        adapter=adapter,
        tracer=tracer,
        model="test-model",
        gather_fn=gather_tracker,
        max_rounds=4,
    )

    slide = result.outline.slides[0]
    # paper_id clamped to None
    assert slide.paper_id is None
    # figure_key clamped to None
    assert slide.figure_key is None
    # grounding empty (aim never gathered)
    assert slide.grounding_chunk_ids == []

    # Verify the trace records the dropped items
    async with conn.execute(
        "SELECT result_summary_json FROM tool_calls ORDER BY step_index DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    trace_result = json.loads(row[0])
    dropped = trace_result["dropped"]
    # Should record the bad paper_id, figure_key, and ungathered aim
    assert any("999" in d or "paper_id=999" in d for d in dropped)
    assert any("bad-key" in d or "figure_key" in d for d in dropped)
    assert any("NEVER_GATHERED" in d for d in dropped)


# ---------------------------------------------------------------------------
# Test 4: prompt slots check
# ---------------------------------------------------------------------------

def test_outline_prompt_loads_and_has_slots() -> None:
    from paperhub.llm.prompts.registry import PromptRegistry
    slot = PromptRegistry().get("slides_outline/v1")
    assert slot.system.strip()
    for key in (
        "{task_description}", "{response_language}", "{seed_map_block}",
        "{gathered_block}", "{round_number}", "{max_rounds}", "{target_slides}",
        "{must_finalize}",
    ):
        assert key in slot.user_template, f"missing slot {key!r}"


# ---------------------------------------------------------------------------
# Test 5: wiring guard
# ---------------------------------------------------------------------------

def test_generate_calls_run_sl_outline() -> None:
    """Guard: the GENERATE node must invoke run_sl_outline and pass an outline
    into run_slide_agent (the F6.1 wiring)."""
    src = inspect.getsource(rg)
    assert "run_sl_outline(" in src
    assert "outline=" in src
