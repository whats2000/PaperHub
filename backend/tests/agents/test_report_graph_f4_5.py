"""F4.5 report_graph — flat 3-step orchestrator end-to-end.

Drives the rebuilt ``build_report_subgraph`` (gather_context fan-out →
slide_agent → sl_emit) with every LLM/IO stage stubbed via monkeypatch.

Asserts:
  * The orchestrator wires the three stages in order.
  * A successful run persists a ``decks`` row with ``current_version_id``
    set (sl_emit stamps it) and ``deck_slides`` rows for each frame.
  * The final ``deck`` SSE event is streamed before the final-response value.
  * The standard ``empty`` (no enabled papers) and ``no_latex`` (pdflatex
    missing) gates still short-circuit.

DO NOT touch the existing R1 test files (test_report_graph_round1_integration
+ test_sl_paper_brief / test_sl_plan_deck / test_sl_render_slide) — Phase 14
deletes them. This file is the new F4.5 surface.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

import paperhub.agents.report_graph as rg
from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.agents.slide_agent import SlideAgentResult
from paperhub.db.migrate import apply_schema
from paperhub.models.domain import RoutingDecision
from paperhub.models.slide_domain import (
    CompileCheckResult,
    FigureDimensions,
    KeyFigureBundle,
    PaperContextBundle,
)
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    SectionAsset,
    write_paper_asset,
)
from paperhub.tracing.tracer import Tracer

# ─────────────────────────── fixtures ────────────────────────────


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = tmp_path / "test.db"
    async with aiosqlite.connect(str(db)) as c:
        await c.execute("PRAGMA foreign_keys = ON")
        await apply_schema(c)
        await c.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (1, datetime('now'), 't')"
        )
        await c.execute(
            "INSERT INTO runs (id, session_id, started_at, status) "
            "VALUES (1, 1, datetime('now'), 'running')"
        )
        await c.commit()
        yield c


def _seed_asset(source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = source_dir / "asset" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    (fig_dir / "fig-000.png").write_bytes(b"\x89PNG\r\n")
    write_paper_asset(
        PaperAsset(
            figures=[
                FigureAsset(
                    id="fig-000",
                    caption="Architecture diagram.",
                    page=1,
                    section="Method",
                    image_path="figures/fig-000.png",
                )
            ],
            sections=[SectionAsset(name="Method", order=0)],
        ),
        source_dir,
    )


async def _seed_paper(conn: aiosqlite.Connection, tmp_path: Path) -> Path:
    source = tmp_path / "papA" / "source"
    _seed_asset(source)
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "authors_json, year, source_path, source_dir_path, html_path) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "arxiv:1",
            "arxiv",
            "2403.01",
            "Paper A",
            "Abstract A",
            '["Alice"]',
            2025,
            "p",
            str(source),
            "h",
        ),
    )
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1,1,1)"
    )
    await conn.commit()
    return source


def _deps(adapter: Any, tracer: Tracer, conn: aiosqlite.Connection, tmp_path: Path) -> ReportDeps:
    return ReportDeps(
        adapter=adapter,
        tracer=tracer,
        conn=conn,
        workspace=tmp_path,
        plan_model="m",
        section_model="m",
        notes_model="m",
        resolve_model="m",
        recall_enabled=False,
    )


def _state() -> dict[str, Any]:
    return {
        "run_id": 1,
        "branch": "",
        "session_id": 1,
        "user_message": "Make a deck.",
        "effective_query": "Make a deck.",
        "response_language": "English",
        "routing_decision": RoutingDecision(
            intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"
        ),
    }


class _NullAdapter:
    """F4.5 _generate does NOT call LlmAdapter (slide_agent + gather_context own
    their litellm calls). _resolve still calls detect_slide_language via the
    adapter for TargetLanguage; we accept that response_model and return None.
    F6.1: sl_outline calls adapter.structured with RoundAction; return a
    finalize action with a minimal valid outline so the GENERATE flow proceeds
    without an LLM.
    """

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        from paperhub.models.domain import TargetLanguage
        from paperhub.models.slide_domain import DeckOutlineDraft, OutlineSlideDraft, RoundAction

        if response_model is TargetLanguage:
            return TargetLanguage(language=None)
        if response_model is RoundAction:
            return RoundAction(
                action="finalize",
                narrative_pattern="synthesis",
                outline=DeckOutlineDraft(
                    talk_title="Stub Talk",
                    audience_intent="stub",
                    narrative_arc="stub",
                    narrative_pattern="synthesis",
                    slides=[OutlineSlideDraft(goal="title", key_message="")],
                ),
            )
        raise AssertionError(
            f"_NullAdapter.structured got unexpected response_model: {response_model!r}"
        )

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            yield ""

        return g()


# ───────────────────────── tests ────────────────────────────────


@pytest.mark.asyncio
async def test_generate_invokes_three_stages_in_order_and_persists_deck(
    conn: aiosqlite.Connection,
    fake_tracer: Tracer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gather_context → slide_agent → sl_emit happy path: each stage stubbed.

    Asserts the final-response yields, a decks row gets a non-null
    current_version_id, and deck_slides has one row per frame.
    """
    source_dir = await _seed_paper(conn, tmp_path)
    assert source_dir.exists()

    calls: list[str] = []

    async def fake_gather(**kw: Any) -> PaperContextBundle:
        calls.append("gather")
        return PaperContextBundle(
            paper_id=kw["paper_id"],
            paper_idx=kw["paper_idx"],
            title=kw["paper_title"],
            authors=kw["paper_authors"],
            year=kw["paper_year"],
            narrative_summary="x",
            key_figures=[
                KeyFigureBundle(
                    key=f"p{kw['paper_idx']}-fig-000",
                    role="overview",
                    one_line_interpretation="x",
                    dimensions=FigureDimensions(width_px=1000, height_px=800),
                )
            ],
            key_equations=[],
            section_excerpts=[],
            paper_newcommands=[],
        )

    async def fake_agent(**kw: Any) -> SlideAgentResult:
        calls.append("agent")
        # Write a one-frame deck.tex matching what sl_emit will read.
        return SlideAgentResult(
            deck_tex=(
                r"\documentclass{beamer}"
                r"\begin{document}"
                r"\begin{frame}{x}body\end{frame}"
                r"\end{document}"
            ),
            preamble=r"\documentclass{beamer}",
            satisfied=True,
            tool_calls_used=3,
            last_compile_check=CompileCheckResult(
                ok=True,
                page_count=1,
                compile_errors=[],
                frame_overflow=[],
                unrendered_math_frames=[],
            ),
            preamble_persisted=False,
        )

    monkeypatch.setattr(rg, "run_gather_context", fake_gather)
    monkeypatch.setattr(rg, "run_slide_agent", fake_agent)
    # Bypass the pdflatex CLI check so the gate doesn't short-circuit.
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)

    deps = _deps(_NullAdapter(), fake_tracer, conn, tmp_path)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    graph = build_report_subgraph(deps)
    deck_events: list[dict[str, Any]] = []
    final_text = ""
    async for mode, payload in graph.astream(
        state, stream_mode=["custom", "values"]
    ):
        if mode == "custom" and isinstance(payload, dict):
            if payload.get("event") == "deck":
                deck_events.append(payload["deck"])
        elif (
            mode == "values"
            and isinstance(payload, dict)
            and "final_response" in payload
        ):
            final_text = payload["final_response"]

    # Three F4.5 stages fired (gather is per-paper, so once for one paper).
    assert calls == ["gather", "agent"]
    # sl_emit persisted the deck (current_version_id stamped, page_count > 0).
    async with conn.execute(
        "SELECT id, page_count, current_version_id, status "
        "FROM decks WHERE session_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    deck_id, page_count, current_version_id, status = row
    assert current_version_id is not None
    assert page_count == 1
    assert status == "ok"
    # deck_slides rebuilt from the audited frames.
    async with conn.execute(
        "SELECT COUNT(*) FROM deck_slides WHERE deck_id = ?", (deck_id,)
    ) as cur:
        n_row = await cur.fetchone()
    assert n_row is not None
    assert n_row[0] == 1
    # The deck SSE event was emitted and the final-response yielded.
    assert deck_events, "no deck event was streamed"
    assert final_text, "no final_response was produced"


@pytest.mark.asyncio
async def test_generate_short_circuits_when_no_enabled_papers(
    conn: aiosqlite.Connection,
    fake_tracer: Tracer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No enabled papers → empty short-circuit; gather_context never fires."""
    called: list[str] = []

    async def boom_gather(**kw: Any) -> PaperContextBundle:
        called.append("gather")
        raise AssertionError("gather_context must not run with no papers")

    monkeypatch.setattr(rg, "run_gather_context", boom_gather)
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)

    deps = _deps(_NullAdapter(), fake_tracer, conn, tmp_path)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    graph = build_report_subgraph(deps)
    final = ""
    async for mode, payload in graph.astream(
        state, stream_mode=["custom", "values"]
    ):
        if (
            mode == "values"
            and isinstance(payload, dict)
            and "final_response" in payload
        ):
            final = payload["final_response"]
    assert not called
    assert "enabled reference" in final.lower() or "reference" in final.lower()


@pytest.mark.asyncio
async def test_generate_short_circuits_when_pdflatex_missing(
    conn: aiosqlite.Connection,
    fake_tracer: Tracer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pdflatex absent → no_latex short-circuit; gather_context never fires."""
    await _seed_paper(conn, tmp_path)

    called: list[str] = []

    async def boom_gather(**kw: Any) -> PaperContextBundle:
        called.append("gather")
        raise AssertionError("gather_context must not run without pdflatex")

    monkeypatch.setattr(rg, "run_gather_context", boom_gather)
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: False)

    deps = _deps(_NullAdapter(), fake_tracer, conn, tmp_path)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    graph = build_report_subgraph(deps)
    final = ""
    async for mode, payload in graph.astream(
        state, stream_mode=["custom", "values"]
    ):
        if (
            mode == "values"
            and isinstance(payload, dict)
            and "final_response" in payload
        ):
            final = payload["final_response"]
    assert not called
    assert "latex" in final.lower()


@pytest.mark.asyncio
async def test_generate_injects_title_metadata_into_preamble(
    conn: aiosqlite.Connection,
    fake_tracer: Tracer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """report_graph._generate must inject \\title{...}/\\author{...}/\\date{...}
    into the preamble passed to slide_agent — derived from paper_content rows.
    Otherwise \\titlepage renders blank (F4.5 regression: F4.2's
    build_title_metadata wiring was dropped by the Phase 10 rewrite)."""
    # Seed one paper with a known title/authors/year/arxiv_id.
    source = tmp_path / "papA" / "source"
    _seed_asset(source)
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "authors_json, year, source_path, source_dir_path, html_path) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "arxiv:2509.22093v1",
            "arxiv",
            "2509.22093v1",
            "Action-aware Dynamic Pruning for VLA",
            "abs",
            '["Feng Pei", "Jiawei Chen"]',
            2025,
            "p",
            str(source),
            "h",
        ),
    )
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1,1,1)"
    )
    await conn.commit()

    captured_preambles: list[str] = []

    async def fake_gather(**kw: Any) -> PaperContextBundle:
        return PaperContextBundle(
            paper_id=kw["paper_id"],
            paper_idx=kw["paper_idx"],
            title=kw["paper_title"],
            authors=kw["paper_authors"],
            year=kw["paper_year"],
            narrative_summary="x",
            key_figures=[],
            key_equations=[],
            section_excerpts=[],
            paper_newcommands=[],
        )

    async def fake_agent(**kw: Any) -> SlideAgentResult:
        captured_preambles.append(kw["resolved_preamble"])
        return SlideAgentResult(
            deck_tex=(
                r"\documentclass{beamer}"
                r"\begin{document}"
                r"\begin{frame}{x}body\end{frame}"
                r"\end{document}"
            ),
            preamble="",
            satisfied=True,
            tool_calls_used=1,
            last_compile_check=CompileCheckResult(
                ok=True,
                page_count=1,
                compile_errors=[],
                frame_overflow=[],
                unrendered_math_frames=[],
            ),
            preamble_persisted=False,
        )

    monkeypatch.setattr(rg, "run_gather_context", fake_gather)
    monkeypatch.setattr(rg, "run_slide_agent", fake_agent)
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)

    deps = _deps(_NullAdapter(), fake_tracer, conn, tmp_path)
    state = _state()
    state["run_id"] = fake_tracer.run_id
    state["user_message"] = "Generate slides for this paper."
    state["effective_query"] = "Generate slides for this paper."

    graph = build_report_subgraph(deps)
    async for _mode, _payload in graph.astream(
        state, stream_mode=["custom", "values"]
    ):
        pass

    assert len(captured_preambles) == 1
    preamble = captured_preambles[0]
    # \title must match the paper's title (single-paper logic).
    assert "\\title{Action-aware Dynamic Pruning for VLA}" in preamble
    # \author must list at least one surname.
    assert "\\author{" in preamble
    assert "Pei" in preamble or "Chen" in preamble
    # \date must include the arxiv id + year.
    assert "\\date{arXiv:2509.22093v1 (2025)}" in preamble


@pytest.mark.asyncio
async def test_generate_multi_paper_uses_user_message_as_talk_title(
    conn: aiosqlite.Connection,
    fake_tracer: Tracer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For multi-paper decks, the talk_title comes from the title synthesizer
    (a small LLM call over the gathered bundles + user message) — NOT the
    user message verbatim — and \\author lists each paper's lead-author surname."""
    for pid, (title, authors_json, surname_dir) in enumerate(
        [
            ("Paper One", '["Anna Alice"]', "papA"),
            ("Paper Two", '["Brian Bob"]', "papB"),
            ("Paper Three", '["Cara Carol"]', "papC"),
        ],
        start=1,
    ):
        source = tmp_path / surname_dir / "source"
        _seed_asset(source)
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
            "abstract, authors_json, year, source_path, source_dir_path, html_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"arxiv:p{pid}",
                "arxiv",
                f"p{pid}",
                title,
                "",
                authors_json,
                2025,
                "p",
                str(source),
                "h",
            ),
        )
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) "
            "VALUES (1, ?, 1)",
            (pid,),
        )
    await conn.commit()

    captured_preambles: list[str] = []

    async def fake_gather(**kw: Any) -> PaperContextBundle:
        return PaperContextBundle(
            paper_id=kw["paper_id"],
            paper_idx=kw["paper_idx"],
            title=kw["paper_title"],
            authors=kw["paper_authors"],
            year=kw["paper_year"],
            narrative_summary="x",
            key_figures=[],
            key_equations=[],
            section_excerpts=[],
            paper_newcommands=[],
        )

    async def fake_agent(**kw: Any) -> SlideAgentResult:
        captured_preambles.append(kw["resolved_preamble"])
        return SlideAgentResult(
            deck_tex=(
                r"\documentclass{beamer}"
                r"\begin{document}"
                r"\begin{frame}{x}body\end{frame}"
                r"\end{document}"
            ),
            preamble="",
            satisfied=True,
            tool_calls_used=1,
            last_compile_check=CompileCheckResult(
                ok=True,
                page_count=1,
                compile_errors=[],
                frame_overflow=[],
                unrendered_math_frames=[],
            ),
            preamble_persisted=False,
        )

    # Patch the title synthesizer to return a deterministic synthesized title
    # so the assertion is on the integration, not on a live LLM.
    async def fake_synth(**kw: Any) -> str:
        return "Efficient VLA: Cross-Paper Synthesis"

    import paperhub.agents.title_synthesizer as ts

    monkeypatch.setattr(ts, "synthesize_talk_title", fake_synth)
    monkeypatch.setattr(rg, "run_gather_context", fake_gather)
    monkeypatch.setattr(rg, "run_slide_agent", fake_agent)
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)

    deps = _deps(_NullAdapter(), fake_tracer, conn, tmp_path)
    state = _state()
    state["run_id"] = fake_tracer.run_id
    user_msg = "Cross-paper synthesis: 12-minute conference talk on efficient VLA"
    state["user_message"] = user_msg
    state["effective_query"] = user_msg

    graph = build_report_subgraph(deps)
    async for _mode, _payload in graph.astream(
        state, stream_mode=["custom", "values"]
    ):
        pass

    assert len(captured_preambles) == 1
    preamble = captured_preambles[0]
    # \title must come from the synthesizer, NOT the raw user message.
    assert "\\title{Efficient VLA: Cross-Paper Synthesis}" in preamble
    # \author should list the three lead-author surnames.
    assert "\\author{" in preamble
    assert "Alice" in preamble and "Bob" in preamble and "Carol" in preamble
