from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.agents.sl_outline import run_sl_outline
from paperhub.db.migrate import apply_schema
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.slide_domain import (
    DeckOutline,
    DeckOutlineDraft,
    OutlineSlideDraft,
    PaperContextBundle,
)
from paperhub.tracing.tracer import Tracer


def test_outline_prompt_loads_and_has_slots() -> None:
    slot = PromptRegistry().get("slides_outline/v1")
    assert slot.system.strip()
    for key in ("{task_description}", "{response_language}", "{bundles_block}", "{n_bundles}"):
        assert key in slot.user_template


class _StubAdapter:
    """Returns a fixed DeckOutlineDraft; records the variables it was called with."""

    def __init__(self, draft: DeckOutlineDraft) -> None:
        self._draft = draft
        self.last_variables: dict | None = None

    async def structured(self, *, slot, variables, response_model, model, **kw):
        assert slot == "slides_outline/v1"
        assert response_model is DeckOutlineDraft
        self.last_variables = variables
        return self._draft


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(tmp_path / "t.db")) as c:
        await apply_schema(c)
        # paper_content: kind='arxiv' requires arxiv_id (CHECK: exactly one of arxiv_id/sha256)
        await c.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
            "VALUES (73, 'k73', 'arxiv', '2301.00001', 'P73', '/p', '/d', '/h')"
        )
        for cid, sec in [(101, "Method"), (102, "Method"), (103, "Intro")]:
            await c.execute(
                "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
                "VALUES (?, 73, ?, 0, 1, 'x')",
                (cid, sec),
            )
        # Insert chat_sessions + runs rows so Tracer can write tool_calls
        await c.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await c.execute("INSERT INTO runs (session_id) VALUES (1)")
        await c.commit()
        yield c


def _tracer(c: aiosqlite.Connection) -> Tracer:
    return Tracer(c, run_id=1, branch="")


def _bundle() -> PaperContextBundle:
    return PaperContextBundle(
        paper_id=73, paper_idx=0, title="P73", authors=["A"], year=2025,
        narrative_summary="...", key_figures=[], key_equations=[],
        section_excerpts=[], paper_newcommands=[],
    )


@pytest.mark.asyncio
async def test_resolves_grounding_sections_to_chunk_ids(conn) -> None:
    draft = DeckOutlineDraft(
        talk_title="T", audience_intent="ai", narrative_arc="arc",
        slides=[
            OutlineSlideDraft(goal="title", key_message=""),
            OutlineSlideDraft(goal="method", key_message="k",
                              paper_id=73, grounding_sections=["Method"]),
        ],
    )
    adapter = _StubAdapter(draft)
    out = await run_sl_outline(
        bundles=[_bundle()], task_description="present these",
        response_language="English", adapter=adapter, tracer=_tracer(conn),
        model="m", conn=conn,
    )
    assert isinstance(out, DeckOutline)
    assert [s.slide_index for s in out.slides] == [0, 1]
    assert out.slides[0].grounding_chunk_ids == []
    assert out.slides[1].grounding_chunk_ids == [101, 102]


@pytest.mark.asyncio
async def test_unknown_section_is_dropped(conn) -> None:
    draft = DeckOutlineDraft(
        talk_title="T", audience_intent="ai", narrative_arc="arc",
        slides=[OutlineSlideDraft(goal="g", key_message="k", paper_id=73,
                                  grounding_sections=["Method", "Nonexistent"])],
    )
    out = await run_sl_outline(
        bundles=[_bundle()], task_description="x", response_language="English",
        adapter=_StubAdapter(draft), tracer=_tracer(conn), model="m", conn=conn,
    )
    assert out.slides[0].grounding_chunk_ids == [101, 102]
