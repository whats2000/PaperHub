"""Tests for the Report Agent GENERATE chain (Plan F3/F4 + F4.4 Round 1 T5).

F4.4 Round 1 (T5) swapped the per-paper ``understand → narrate → draft``
chain for an agentic-brief topology (``sl_paper_brief → sl_plan_deck →
sl_render_slide``). These tests stub the three new nodes directly on the
``report_graph`` module so the suite stays litellm-free, mirrors the
new tool-call names (``report:paper_brief`` / ``report:plan_deck`` /
``report:render_slide``), and asserts on the same end-to-end contracts
the prior chain was held to (figure-key staging, no-hallucination guard,
per-stage streaming, deck_slides rows).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import paperhub.agents.report_graph as rg
from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.db.deck_slides import get_deck_slides
from paperhub.db.decks import get_deck
from paperhub.models.domain import (
    DeckOutline,
    KeyEquation,
    KeyFigure,
    KeyResult,
    PaperTalkBrief,
    PlannedSlide,
    RenderedSlide,
    RoutingDecision,
    TargetLanguage,
)
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
    write_paper_asset,
)


def _seed_asset(source_dir: Path) -> None:
    """Write a PaperAsset to disk with one real figure file."""
    fig_dir = paper_asset_dir(source_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    (fig_dir / "fig-000.png").write_bytes(b"\x89PNG\r\n")
    write_paper_asset(
        PaperAsset(
            figures=[
                FigureAsset(
                    id="fig-000",
                    caption="The architecture diagram.",
                    page=1,
                    section="Method",
                    image_path="figures/fig-000.png",
                )
            ],
            sections=[SectionAsset(name="Method", order=0)],
        ),
        source_dir,
    )


def _brief(paper_id: int, *, figure_key: str = "p0-fig-000") -> PaperTalkBrief:
    return PaperTalkBrief(
        paper_id=paper_id,
        contribution=f"Paper {paper_id} contributes X.",
        method_core="Scaled attention.",
        key_results=[
            KeyResult(description="SOTA", number="14%", benchmark="LIBERO"),
        ],
        key_figures=[
            KeyFigure(
                key=figure_key,
                role="overview",
                one_line_interpretation="It shows the thing.",
            )
        ],
        key_equations=[
            KeyEquation(
                latex="E=mc^2",
                role="objective",
                notation_explanation="E is energy.",
            )
        ],
        paper_newcommands="",
        talk_shape_hint="concept+math",
    )


def _outline_two_concept_slides(
    *,
    paper_id: int,
    good_figure_key: str,
    ghost_figure_key: str,
) -> DeckOutline:
    """A 2-slide outline used by the happy-path + no-hallucination tests.

    Slide 0 references the staged inventory key (real figure file on disk);
    slide 1 references a ghost key that ``sl_verify_figures`` must rewrite
    to ``[figure omitted]`` (the no-hallucination guard).
    """
    return DeckOutline(
        talk_title="MoE",
        slides=[
            PlannedSlide(
                pattern_kind="concept_2col",
                title="Motivation",
                goal="why",
                paper_id=paper_id,
                figure_key=good_figure_key,
            ),
            PlannedSlide(
                pattern_kind="concept_2col",
                title="Method",
                goal="how",
                paper_id=paper_id,
                # NOTE: T2's _validate_attributions would normally reject this
                # because ``ghost`` is not in the brief's key_figures; the
                # planner stub bypasses that validator, so we get the same
                # adversarial path the OLD test exercised — verify_figures
                # is the final defence.
                figure_key=ghost_figure_key,
            ),
        ],
        style_profile_name="default",
    )


def _frame_tex(planned: PlannedSlide) -> str:
    """A minimal-but-valid concept_2col frame for the stub renderer."""
    key = planned.figure_key or ""
    return (
        "\\begin{frame}{" + planned.title + "}"
        "\\includegraphics{" + key + "}"
        "\\end{frame}"
    )


def _install_chain_stubs(
    monkeypatch: Any,
    *,
    briefs: list[PaperTalkBrief],
    outline: DeckOutline,
    render_hook: Any = None,
) -> dict[str, Any]:
    """Patch the three new agentic nodes in ``report_graph`` so the test
    drives the chain without a real LLM. ``render_hook`` (optional) is
    awaited per render call AFTER the tracer step opens — lets a test
    block one render to assert sibling streaming."""
    captured: dict[str, Any] = {"renders": 0}
    by_id = {b.paper_id: b for b in briefs}

    async def fake_paper_brief(*, paper_content_id, paper_idx, title,
                               tracer, model, conn, **kw):  # type: ignore[no-untyped-def]
        async with tracer.step(
            agent="report", tool="report:paper_brief", model=model,
        ) as step:
            step.record_args({"paper_content_id": paper_content_id})
            step.record_result({"stubbed": True})
        return by_id[paper_content_id]

    async def fake_plan_deck(*, briefs, target_slide_count, talk_title_hint,
                             tracer, model, **kw):  # type: ignore[no-untyped-def]
        async with tracer.step(
            agent="report", tool="report:plan_deck", model=model,
        ) as step:
            step.record_args({"target": target_slide_count})
            step.record_result({"stubbed": True})
        return outline

    async def fake_render_slide(*, planned_slide, deck_outline, paper_brief,
                                all_briefs, tracer, model, **kw):  # type: ignore[no-untyped-def]
        captured["renders"] += 1
        slide_idx = next(
            i for i, s in enumerate(deck_outline.slides) if s is planned_slide
        )
        frame_tex = _frame_tex(planned_slide)
        figure_keys = (
            [planned_slide.figure_key] if planned_slide.figure_key else []
        )
        async with tracer.step(
            agent="report", tool="report:render_slide", model=model,
        ) as step:
            step.record_args({
                "slide_index": slide_idx,
                "pattern_kind": planned_slide.pattern_kind,
            })
            if render_hook is not None:
                await render_hook(slide_idx)
            step.record_result({"stubbed": True})
        return RenderedSlide(
            slide_index=slide_idx,
            pattern_kind=planned_slide.pattern_kind,
            paper_id=planned_slide.paper_id,
            frame_tex=frame_tex,
            figure_keys_used=figure_keys,
            callback_reads=[],
        )

    monkeypatch.setattr(rg, "run_sl_paper_brief", fake_paper_brief)
    monkeypatch.setattr(rg, "run_sl_plan_deck", fake_plan_deck)
    monkeypatch.setattr(rg, "run_sl_render_slide", fake_render_slide)
    return captured


class _CoherenceEchoAdapter:
    """The T5 chain uses the adapter for (a) ``TargetLanguage`` via
    ``detect_slide_language`` in ``_resolve`` and (b) the streamed
    ``slides_coherence/v1`` slot. The deprecated ``PaperBrief`` /
    ``TalkOutline`` / ``FrameDraft`` schemas must never be requested —
    any such call signals an OLD-chain leak and is surfaced immediately.
    """

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is TargetLanguage:
            return TargetLanguage(language=None)
        raise AssertionError(
            f"adapter.structured got an unexpected response_model under "
            f"the T5 chain: {response_model!r}"
        )

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_coherence/v1":
                yield kw["variables"]["frames_block"]

        return g()


def _make_deps(adapter, fake_tracer, migrated_db, retriever, tmp_path) -> ReportDeps:  # type: ignore[no-untyped-def]
    return ReportDeps(
        adapter=adapter,
        tracer=fake_tracer,
        conn=migrated_db,
        retriever=retriever,
        workspace=tmp_path,
        plan_model="m",
        section_model="m",
        notes_model="m",
        resolve_model="m",
        recall_enabled=False,
    )


def _state() -> dict[str, Any]:
    return {
        "run_id": 0,  # overwritten by caller
        "branch": "",
        "session_id": 1,
        "user_message": "make slides",
        "effective_query": "make slides comparing these",
        "response_language": "English",
        "routing_decision": RoutingDecision(
            intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"
        ),
    }


async def _insert_one_paper(migrated_db: Any, source_dir: Path,
                            *, content_key: str = "arxiv:1",
                            arxiv_id: str = "2403.01",
                            title: str = "Paper A") -> None:
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, 'arxiv', ?, ?, 'An abstract.', 'p', ?, 'h')",
        (content_key, arxiv_id, title, str(source_dir)),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)"
    )
    await migrated_db.commit()


@pytest.mark.asyncio
async def test_create_deck_happy_path(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    source_dir = tmp_path / "cacheA" / "source"
    _seed_asset(source_dir)
    await _insert_one_paper(migrated_db, source_dir)

    briefs = [_brief(paper_id=1)]
    outline = _outline_two_concept_slides(
        paper_id=1, good_figure_key="p0-fig-000", ghost_figure_key="ghost",
    )
    _install_chain_stubs(monkeypatch, briefs=briefs, outline=outline)

    captured: dict[str, str] = {}

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        captured["tex"] = tex
        return compile_mod.CompileResult(True, 1, tex, "", 2)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    deps = _make_deps(
        _CoherenceEchoAdapter(), fake_tracer, migrated_db, None, tmp_path,
    )
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id
    events: list[Any] = []
    last_values: dict[str, Any] = {}
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(payload)
        elif mode == "values" and isinstance(payload, dict):
            last_values = payload

    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    assert deck.status == "ok"
    assert deck.page_count > 0
    assert any(e.get("event") == "deck" for e in events)
    # The assigned (real) figure was staged under its deck-unique key.
    figures_dir = tmp_path / "chat_session" / "1" / "slides" / "figures"
    assert (figures_dir / "p0-fig-000.png").exists()
    assert (tmp_path / "chat_session" / "1" / "slides" / "deck.pdf").exists()
    # F4: GENERATE produces slides-only — notes are opt-in, written by a later sub-flow.
    assert deck.speaker_notes == {}, "GENERATE path must not write speaker notes"
    rows = await get_deck_slides(migrated_db, deck_id=deck.id)
    assert len(rows) == deck.page_count, "one deck_slides row per compiled page"
    assert all(r.note_text is None for r in rows), "no notes in GENERATE path"
    # The deck event must report has_notes=False.
    deck_evt = next(e for e in events if e.get("event") == "deck")
    assert deck_evt["deck"]["has_notes"] is False
    # The finalize message mentions speaker notes (hint at the notes sub-flow).
    final = last_values.get("final_response", "")
    assert "Generated" in final
    assert "speaker notes" in final.lower(), f"hint missing from: {final!r}"


@pytest.mark.asyncio
async def test_per_stage_tool_step_events_stream_before_deck(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """The graph must emit multiple ``tool_step`` custom events DURING the
    run (one per traced stage) BEFORE the final ``deck`` event — proving the
    trace panel streams live, not just the deck at the end."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    source_dir = tmp_path / "cacheS" / "source"
    _seed_asset(source_dir)
    await _insert_one_paper(migrated_db, source_dir)

    briefs = [_brief(paper_id=1)]
    outline = _outline_two_concept_slides(
        paper_id=1, good_figure_key="p0-fig-000", ghost_figure_key="ghost",
    )
    _install_chain_stubs(monkeypatch, briefs=briefs, outline=outline)

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, tex, "", 2)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    deps = _make_deps(
        _CoherenceEchoAdapter(), fake_tracer, migrated_db, None, tmp_path,
    )
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    # Record the ORDER custom events arrive so we can assert tool_step before deck.
    order: list[str] = []
    tool_steps: list[dict[str, Any]] = []
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode != "custom":
            continue
        evt = payload.get("event")
        order.append(evt)
        if evt == "tool_step":
            tool_steps.append(payload["record"])

    # At least 3 tool_step events streamed during the run.
    assert len(tool_steps) >= 3, f"expected >=3 tool_step events, got {len(tool_steps)}"

    # The first deck event arrives AFTER at least 3 tool_step events.
    assert "deck" in order, "no deck event emitted"
    deck_pos = order.index("deck")
    steps_before_deck = sum(1 for e in order[:deck_pos] if e == "tool_step")
    assert steps_before_deck >= 3, (
        f"expected >=3 tool_step events before deck, got {steps_before_deck}"
    )

    # The per-stage stages are represented — new agentic-brief tool names.
    tool_names = {r["tool"] for r in tool_steps}
    assert {
        "report:paper_brief", "report:plan_deck", "report:render_slide"
    } & tool_names, (
        f"expected per-stage T5 tool names, got {tool_names}"
    )

    # No record is emitted twice (dedupe by step_index).
    step_indices = [r["step_index"] for r in tool_steps]
    assert len(step_indices) == len(set(step_indices)), "duplicate tool_step emitted"


@pytest.mark.asyncio
async def test_render_tool_step_streams_before_sibling_completes(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """A render's ``tool_step`` must stream the instant THAT render completes —
    not batched until the whole ``sl_render_slide`` fan-out resolves.

    A render hook blocks the second render on an event that the consumer
    sets only upon receiving the first ``report:render_slide`` tool_step.
    With a burst-at-gather bug, no render step is emitted until BOTH
    renders finish, so the second render blocks forever and the run
    deadlocks (caught by ``wait_for`` timeout). With per-task streaming
    the first step arrives, the event is set, the second render proceeds,
    and the run completes."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    source_dir = tmp_path / "cacheG" / "source"
    _seed_asset(source_dir)
    await _insert_one_paper(migrated_db, source_dir)

    briefs = [_brief(paper_id=1)]
    outline = _outline_two_concept_slides(
        paper_id=1, good_figure_key="p0-fig-000", ghost_figure_key="ghost",
    )
    release = asyncio.Event()

    async def render_hook(slide_idx: int) -> None:
        if slide_idx >= 1:
            await release.wait()

    _install_chain_stubs(
        monkeypatch, briefs=briefs, outline=outline, render_hook=render_hook,
    )

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, tex, "", 2)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    deps = _make_deps(
        _CoherenceEchoAdapter(), fake_tracer, migrated_db, None, tmp_path,
    )
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    saw_render_step = False

    async def _consume() -> None:
        nonlocal saw_render_step
        async for mode, payload in graph.astream(
            state, stream_mode=["custom", "values"]
        ):
            if mode != "custom" or payload.get("event") != "tool_step":
                continue
            if (
                payload["record"]["tool"] == "report:render_slide"
                and not saw_render_step
            ):
                saw_render_step = True
                # Release the blocked sibling render; if the bug batches steps
                # at the gather boundary this line is never reached → timeout.
                release.set()

    await asyncio.wait_for(_consume(), timeout=10)

    assert saw_render_step, (
        "no report:render_slide tool_step streamed during the run"
    )
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None and deck.status == "ok"


@pytest.mark.asyncio
async def test_no_hallucination_unknown_figure_neutralized(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """``sl_verify_figures`` must rewrite any ``\\includegraphics`` whose key
    is not in the deck-wide inventory to ``[figure omitted]``.

    The render stub here emits a hardcoded ``\\includegraphics{ghost}`` in
    one of its frames — independent of the PlannedSlide's ``figure_key`` —
    so the post-render verify step is the surface under test (NOT the
    planner's pre-render sanitization)."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    source_dir = tmp_path / "cacheA" / "source"
    _seed_asset(source_dir)
    await _insert_one_paper(migrated_db, source_dir)

    briefs = [_brief(paper_id=1)]
    # Single planned slide pointing at the REAL figure key — but the render
    # stub below emits TWO frames, the second carrying a ghost figure
    # reference inside its frame_tex (the no-hallucination contract is on
    # the emitted tex, not the planner's attribution).
    outline = DeckOutline(
        talk_title="MoE",
        slides=[
            PlannedSlide(
                pattern_kind="concept_2col",
                title="Motivation", goal="why",
                paper_id=1, figure_key="p0-fig-000",
            ),
            PlannedSlide(
                pattern_kind="concept_2col",
                title="Method", goal="how",
                paper_id=1, figure_key="p0-fig-000",
            ),
        ],
        style_profile_name="default",
    )

    call_idx = {"n": 0}

    async def fake_paper_brief(*, paper_content_id, paper_idx, title,
                               tracer, model, conn, **kw):  # type: ignore[no-untyped-def]
        async with tracer.step(
            agent="report", tool="report:paper_brief", model=model,
        ) as step:
            step.record_args({"paper_content_id": paper_content_id})
            step.record_result({"stubbed": True})
        return briefs[0]

    async def fake_plan_deck(*, briefs, target_slide_count, talk_title_hint,
                             tracer, model, **kw):  # type: ignore[no-untyped-def]
        async with tracer.step(
            agent="report", tool="report:plan_deck", model=model,
        ) as step:
            step.record_args({"target": target_slide_count})
            step.record_result({"stubbed": True})
        return outline

    async def fake_render_slide(*, planned_slide, deck_outline, paper_brief,
                                all_briefs, tracer, model, **kw):  # type: ignore[no-untyped-def]
        call_idx["n"] += 1
        slide_idx = next(
            i for i, s in enumerate(deck_outline.slides) if s is planned_slide
        )
        # The SECOND render call emits a hallucinated figure key directly
        # in its frame_tex — that's the path sl_verify_figures must catch.
        if call_idx["n"] == 2:
            frame_tex = (
                "\\begin{frame}{" + planned_slide.title + "}"
                "\\includegraphics{ghost}"
                "\\end{frame}"
            )
            figure_keys = ["ghost"]
        else:
            frame_tex = _frame_tex(planned_slide)
            figure_keys = [planned_slide.figure_key] if planned_slide.figure_key else []
        async with tracer.step(
            agent="report", tool="report:render_slide", model=model,
        ) as step:
            step.record_args({
                "slide_index": slide_idx,
                "pattern_kind": planned_slide.pattern_kind,
            })
            step.record_result({"stubbed": True})
        return RenderedSlide(
            slide_index=slide_idx,
            pattern_kind=planned_slide.pattern_kind,
            paper_id=planned_slide.paper_id,
            frame_tex=frame_tex,
            figure_keys_used=figure_keys,
            callback_reads=[],
        )

    monkeypatch.setattr(rg, "run_sl_paper_brief", fake_paper_brief)
    monkeypatch.setattr(rg, "run_sl_plan_deck", fake_plan_deck)
    monkeypatch.setattr(rg, "run_sl_render_slide", fake_render_slide)

    captured: dict[str, str] = {}

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        captured["tex"] = tex
        return compile_mod.CompileResult(True, 1, tex, "", 2)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    deps = _make_deps(
        _CoherenceEchoAdapter(), fake_tracer, migrated_db, None, tmp_path,
    )
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id
    async for _mode, _payload in graph.astream(
        state, stream_mode=["custom", "values"]
    ):
        pass

    # The hallucinated {ghost} include is neutralized; the real one survives.
    tex = captured["tex"]
    assert "{ghost}" not in tex
    assert "[figure omitted]" in tex
    assert "{p0-fig-000}" in tex

    # A report:verify_figures row recorded the rejected key.
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls "
        "WHERE run_id = ? AND tool = 'report:verify_figures'",
        (fake_tracer.run_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    import json

    summary = json.loads(row[0])
    assert "ghost" in summary["rejected"]


@pytest.mark.asyncio
async def test_empty_enabled_set_message(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path
) -> None:
    deps = _make_deps(
        _CoherenceEchoAdapter(), fake_tracer, migrated_db, None, tmp_path,
    )
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id
    final = None
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "values" and isinstance(payload, dict) and payload.get(
            "final_response"
        ):
            final = payload["final_response"]
    assert final is not None and "enable" in final.lower()


@pytest.mark.asyncio
async def test_missing_pdflatex_message(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: False
    )
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1','arxiv','2403.01','A','p',?,'h')",
        (str(tmp_path / "s"),),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1,1,1)"
    )
    await migrated_db.commit()
    deps = _make_deps(
        _CoherenceEchoAdapter(), fake_tracer, migrated_db, None, tmp_path,
    )
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id
    final = None
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "values" and isinstance(payload, dict) and payload.get(
            "final_response"
        ):
            final = payload["final_response"]
    assert final is not None and "latex" in final.lower()


# ---------------------------------------------------------------------------
# Split-frame path: a compile result with 3 pages produces deck_slides rows
# matching the FRAME blocks (not the PDF pages). The fake compile returns a
# tex with \maketitle + two same-frametitle frames; the assemble step
# prepends its own \titlepage frame too, so the split-tex below is what
# the test substitutes via the fake compile.
# ---------------------------------------------------------------------------
_SPLIT_TEX = r"""\documentclass{beamer}
\begin{document}
\maketitle
\begin{frame}{Method}
First continuation of the method slide.
\end{frame}
\begin{frame}{Method}
Second continuation of the method slide.
\end{frame}
\end{document}"""


@pytest.mark.asyncio
async def test_split_frame_deck_slides_written(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """Graph-level integration test: a compile result with 3 pages produces
    deck_slides rows with no notes (F4: slides-only path — notes are opt-in)."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    source_dir = tmp_path / "cacheB" / "source"
    _seed_asset(source_dir)
    await _insert_one_paper(
        migrated_db, source_dir, content_key="arxiv:2",
        arxiv_id="2403.02", title="Paper B",
    )

    briefs = [_brief(paper_id=1)]
    # Single method slide referencing the real figure key.
    outline = DeckOutline(
        talk_title="Method",
        slides=[
            PlannedSlide(
                pattern_kind="concept_2col",
                title="Method",
                goal="explain the method",
                paper_id=1,
                figure_key="p0-fig-000",
                key_points=["step 1", "step 2"],
            ),
        ],
        style_profile_name="default",
    )
    _install_chain_stubs(monkeypatch, briefs=briefs, outline=outline)

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_split_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(_SPLIT_TEX)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, _SPLIT_TEX, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_split_compile)

    deps = _make_deps(
        _CoherenceEchoAdapter(), fake_tracer, migrated_db, None, tmp_path,
    )
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    async for _mode, _payload in graph.astream(
        state, stream_mode=["custom", "values"]
    ):
        pass

    # ---- verify the deck row was written correctly ----
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    assert deck.status == "ok"
    assert deck.page_count == 3

    # F4: notes are empty — opt-in sub-flow only.
    assert deck.speaker_notes == {}, "GENERATE path must not write speaker notes"

    # One deck_slides row per frame block (2 \begin{frame} blocks in _SPLIT_TEX),
    # not per page — page_count=3 includes the \maketitle title page.
    rows = await get_deck_slides(migrated_db, deck_id=deck.id)
    assert len(rows) == 2, f"expected 2 deck_slides rows (2 frame blocks), got {len(rows)}"
    assert all(r.note_text is None for r in rows), "no notes in GENERATE path"
