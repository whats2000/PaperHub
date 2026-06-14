"""Tests for the F6.1-R sl_outline digest-driven orchestrator loop.

Tests are fully deterministic — the LLM adapter and read_fn are stubbed.
Grounding comes from the stubbed ReadResult chunk ids, not real SQL.
"""
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.agents.sl_outline import run_sl_outline
from paperhub.agents.sl_read import ReadResult
from paperhub.db.migrate import apply_schema
from paperhub.models.slide_domain import (
    DeckOutline,
    DeckOutlineDraft,
    DigestSection,
    OutlineResult,
    OutlineSlideDraft,
    PaperDigest,
    ReadRequest,
    RoundAction,
    SeedFigure,
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


def _digests() -> list[PaperDigest]:
    return [
        PaperDigest(
            paper_id=73,
            title="Paper A",
            abstract="Abstract of paper A",
            sections=[
                DigestSection(name="Method", insight="Describes the core method."),
                DigestSection(name="Results", insight="Reports the headline numbers."),
            ],
            figures=[SeedFigure(key="p0-fig-001", caption="Architecture diagram")],
        ),
        PaperDigest(
            paper_id=74,
            title="Paper B",
            abstract="Abstract of paper B",
            sections=[
                DigestSection(name="Approach", insight="Outlines the approach."),
                DigestSection(name="Experiments", insight="Summarizes the experiments."),
            ],
            figures=[SeedFigure(key="p1-fig-001", caption="Results chart")],
        ),
    ]


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
# Stub read_fn
# ---------------------------------------------------------------------------

class _ReadTracker:
    """Records all read_fn calls; returns controlled ReadResults."""

    def __init__(self, results: dict[tuple[int, str], ReadResult]) -> None:
        self._results = results
        self.calls: list[tuple[int, str]] = []

    async def __call__(self, paper_id: int, section: str) -> ReadResult:
        self.calls.append((paper_id, section))
        key = (paper_id, section)
        if key in self._results:
            return self._results[key]
        return ReadResult(text=f"text for {paper_id}:{section}", chunk_ids=[])


# ---------------------------------------------------------------------------
# Test 1: read then finalize
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_then_finalize(conn: aiosqlite.Connection) -> None:
    """Round 1 reads two sections; round 2 finalizes with cites_reads pointing at
    those keys.  Asserts read_fn called correctly, grounding_chunk_ids correct,
    support_excerpts non-empty, speaker_note_hint carried, narrative_pattern
    propagated."""
    chunk_ids_A = [101, 102]
    chunk_ids_B = [201]
    read_tracker = _ReadTracker({
        (73, "Method"): ReadResult(text="Method evidence", chunk_ids=chunk_ids_A),
        (74, "Experiments"): ReadResult(text="Experiments evidence", chunk_ids=chunk_ids_B),
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
                    cites_reads=[],
                ),
                OutlineSlideDraft(
                    goal="Paper A Method",
                    key_message="Key mechanism",
                    content_form="comparison_table",
                    paper_id=73,
                    # spacing/case differs from stored key on purpose
                    cites_reads=["73:method"],
                    speaker_note_hint="Explain how the method works and why it was designed this way.",
                ),
                OutlineSlideDraft(
                    goal="Synthesis",
                    key_message="Combined takeaway",
                    content_form="synthesis",
                    cites_reads=["73:Method", "74:Experiments"],
                ),
            ],
        ),
    )

    script = [
        RoundAction(
            action="read",
            narrative_pattern="comparison",
            reads=[
                ReadRequest(paper_id=73, section_name="Method"),
                ReadRequest(paper_id=74, section_name="Experiments"),
            ],
        ),
        finalize_action,
    ]
    adapter = _ScriptedAdapter(script)
    tracer = _tracer(conn)

    result = await run_sl_outline(
        digests=_digests(),
        task_description="Compare these papers",
        response_language="English",
        target_slides=10,
        adapter=adapter,
        tracer=tracer,
        model="test-model",
        read_fn=read_tracker,
        max_rounds=4,
    )

    # read_fn called once per read in the round
    assert set(read_tracker.calls) == {(73, "Method"), (74, "Experiments")}

    assert isinstance(result, OutlineResult)
    assert result.rounds_used == 2

    outline = result.outline
    assert isinstance(outline, DeckOutline)
    assert outline.narrative_pattern == "comparison"
    assert len(outline.slides) == 3

    # Title slide — no cites_reads, so no grounding
    assert outline.slides[0].grounding_chunk_ids == []

    # Paper A slide cites "73:method" (different case) -> grounds to chunk_ids_A
    method_slide = outline.slides[1]
    assert method_slide.grounding_chunk_ids == sorted(set(chunk_ids_A))
    assert method_slide.support_excerpts  # non-empty
    assert method_slide.speaker_note_hint == (
        "Explain how the method works and why it was designed this way."
    )

    # Synthesis slide cites both reads
    synth_slide = outline.slides[2]
    assert synth_slide.grounding_chunk_ids == sorted(set(chunk_ids_A + chunk_ids_B))


# ---------------------------------------------------------------------------
# Test 2: filtering — empty section, duplicate, unknown paper_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_filtering(conn: aiosqlite.Connection) -> None:
    """A read round with an empty section_name, a duplicate, and a paper_id not
    in the digests — read_fn is NOT called for those."""
    read_tracker = _ReadTracker({
        (73, "Method"): ReadResult(text="Method evidence", chunk_ids=[1]),
    })

    script = [
        RoundAction(
            action="read",
            narrative_pattern="synthesis",
            reads=[
                ReadRequest(paper_id=73, section_name="Method"),
                ReadRequest(paper_id=73, section_name=""),          # empty -> skip
                ReadRequest(paper_id=73, section_name="Method"),    # dup -> skip
                ReadRequest(paper_id=999, section_name="Ghost"),    # unknown paper -> skip
            ],
        ),
        RoundAction(
            action="finalize",
            narrative_pattern="synthesis",
            outline=DeckOutlineDraft(
                talk_title="T",
                audience_intent="i",
                narrative_arc="a",
                slides=[OutlineSlideDraft(goal="Title", key_message="")],
            ),
        ),
    ]
    adapter = _ScriptedAdapter(script)
    tracer = _tracer(conn)

    await run_sl_outline(
        digests=_digests(),
        task_description="Filter test",
        response_language="English",
        target_slides=8,
        adapter=adapter,
        tracer=tracer,
        model="test-model",
        read_fn=read_tracker,
        max_rounds=4,
    )

    # Only the valid, unique read fired
    assert read_tracker.calls == [(73, "Method")]


# ---------------------------------------------------------------------------
# Test 3: forced finalize at budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forced_finalize_at_budget(conn: aiosqlite.Connection) -> None:
    """An adapter that always returns read must not loop forever.
    After max_rounds the loop falls back to a minimal outline and returns."""
    always_read = RoundAction(
        action="read",
        narrative_pattern="synthesis",
        reads=[ReadRequest(paper_id=73, section_name="Method")],
    )
    read_tracker = _ReadTracker({(73, "Method"): ReadResult(text="x", chunk_ids=[999])})

    script = [always_read] * 10  # way more than max_rounds
    adapter = _ScriptedAdapter(script)
    tracer = _tracer(conn)

    result = await run_sl_outline(
        digests=_digests(),
        task_description="Always read",
        response_language="English",
        target_slides=8,
        adapter=adapter,
        tracer=tracer,
        model="test-model",
        read_fn=read_tracker,
        max_rounds=3,
    )

    assert isinstance(result, OutlineResult)
    assert result.rounds_used == 3  # capped at max_rounds
    assert isinstance(result.outline, DeckOutline)
    assert len(result.outline.slides) >= 1  # minimal outline synthesized


# ---------------------------------------------------------------------------
# Test 4: clamp — bad paper_id, figure_key, unread cites_reads recorded in dropped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clamp_bad_paper_figure_read(conn: aiosqlite.Connection) -> None:
    """Finalized slide with paper_id not in digests, figure_key not in digests,
    and a cites_reads key that was never read — all clamped and recorded in the
    trace's 'dropped' field."""
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
                    paper_id=999,                     # NOT in digests
                    figure_key="bad-key",             # NOT in digests
                    cites_reads=["73:NeverRead"],     # never fetched
                ),
                # A second valid content slide so the degenerate-outline gate
                # (min 2 content slides) does NOT fire — this test exercises
                # clamping, not degeneration.
                OutlineSlideDraft(
                    goal="Valid slide",
                    key_message="ok",
                    content_form="bullets",
                    paper_id=73,
                ),
            ],
        ),
    )
    adapter = _ScriptedAdapter([finalize_action])
    read_tracker = _ReadTracker({})
    tracer = _tracer(conn)

    result = await run_sl_outline(
        digests=_digests(),
        task_description="Clamp test",
        response_language="English",
        target_slides=8,
        adapter=adapter,
        tracer=tracer,
        model="test-model",
        read_fn=read_tracker,
        max_rounds=4,
    )

    # slides[0] is now the deterministic front title; the bad content slide is
    # the first non-title slide.
    slide = next(s for s in result.outline.slides if s.content_form != "title")
    assert slide.paper_id is None
    assert slide.figure_key is None
    assert slide.grounding_chunk_ids == []

    # Verify the trace records the dropped items
    async with conn.execute(
        "SELECT result_summary_json FROM tool_calls ORDER BY step_index DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    trace_result = json.loads(row[0])
    dropped = trace_result["dropped"]
    assert any("999" in d or "paper_id=999" in d for d in dropped)
    assert any("bad-key" in d or "figure_key" in d for d in dropped)
    assert any("NeverRead" in d or "neverread" in d.lower() for d in dropped)


# ---------------------------------------------------------------------------
# Test 5: prompt slots check
# ---------------------------------------------------------------------------

def test_outline_prompt_loads_and_has_slots() -> None:
    from paperhub.llm.prompts.registry import PromptRegistry
    slot = PromptRegistry().get("slides_outline/v1")
    assert slot.system.strip()
    for key in (
        "{task_description}", "{response_language}", "{target_slides}",
        "{digest_block}", "{read_block}", "{round_number}", "{max_rounds}",
        "{must_finalize}",
    ):
        assert key in slot.user_template, f"missing slot {key!r}"


# ---------------------------------------------------------------------------
# Test 6: deterministic front-title guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_front_title_prepended_when_missing(conn: aiosqlite.Connection) -> None:
    """An outline whose first slide isn't a title gets a front title prepended
    (the base writer renders 1:1, so a missing title slide = no \\titlepage —
    the live run 569 regression). Slides are renumbered 0..N-1."""
    finalize = RoundAction(
        action="finalize",
        narrative_pattern="single_paper",
        outline=DeckOutlineDraft(
            talk_title="GRAPE",
            narrative_pattern="single_paper",
            audience_intent="x",
            narrative_arc="y",
            slides=[
                OutlineSlideDraft(
                    goal="Motivation", key_message="m",
                    content_form="bullets", paper_id=73,
                ),
                OutlineSlideDraft(
                    goal="Takeaway", key_message="t", content_form="synthesis",
                ),
            ],
        ),
    )
    result = await run_sl_outline(
        digests=_digests(), task_description="t", response_language="English",
        target_slides=8, adapter=_ScriptedAdapter([finalize]), tracer=_tracer(conn),
        model="m", read_fn=_ReadTracker({}), max_rounds=4,
    )
    slides = result.outline.slides
    assert slides[0].content_form == "title"
    assert slides[0].key_message == "GRAPE"  # talk_title carried onto the title
    assert slides[1].goal == "Motivation"    # original first slide shifted down
    assert [s.slide_index for s in slides] == list(range(len(slides)))  # renumbered


@pytest.mark.asyncio
async def test_front_title_not_duplicated(conn: aiosqlite.Connection) -> None:
    """When the outline already opens with a title, no extra title is added."""
    finalize = RoundAction(
        action="finalize",
        narrative_pattern="single_paper",
        outline=DeckOutlineDraft(
            talk_title="GRAPE",
            narrative_pattern="single_paper",
            audience_intent="x",
            narrative_arc="y",
            slides=[
                OutlineSlideDraft(goal="Title", key_message="", content_form="title"),
                OutlineSlideDraft(
                    goal="Body", key_message="m", content_form="bullets", paper_id=73,
                ),
            ],
        ),
    )
    result = await run_sl_outline(
        digests=_digests(), task_description="t", response_language="English",
        target_slides=8, adapter=_ScriptedAdapter([finalize]), tracer=_tracer(conn),
        model="m", read_fn=_ReadTracker({}), max_rounds=4,
    )
    titles = [s for s in result.outline.slides if s.content_form == "title"]
    assert len(titles) == 1
    assert result.outline.slides[0].content_form == "title"




@pytest.mark.asyncio
async def test_degenerate_outline_falls_back_to_minimal(conn: aiosqlite.Connection) -> None:
    """An outline finalized with no content slides (just a title) is degenerate;
    the gate replaces it with a per-paper minimal outline so base_write can't
    ship a 2-page deck (live run 574)."""
    finalize = RoundAction(
        action="finalize",
        narrative_pattern="synthesis",
        outline=DeckOutlineDraft(
            talk_title="T",
            narrative_pattern="synthesis",
            audience_intent="x",
            narrative_arc="y",
            slides=[OutlineSlideDraft(goal="Title", key_message="", content_form="title")],
        ),
    )
    result = await run_sl_outline(
        digests=_digests(), task_description="t", response_language="English",
        target_slides=8, adapter=_ScriptedAdapter([finalize]), tracer=_tracer(conn),
        model="m", read_fn=_ReadTracker({}), max_rounds=4,
    )
    structural = {"title", "section_divider", "agenda"}
    content = [s for s in result.outline.slides if s.content_form not in structural]
    assert len(content) >= 3  # minimal fallback covers both digest papers + synthesis

    async with conn.execute(
        "SELECT result_summary_json FROM tool_calls ORDER BY step_index DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert any("outline-degenerate" in d for d in json.loads(row[0])["dropped"])
