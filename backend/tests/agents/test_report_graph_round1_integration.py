"""F4.4 Round 1 (T5) — end-to-end wiring of the agentic-brief topology.

Drives the FULL new ``sl_resolve → sl_paper_brief → sl_plan_deck →
sl_render_slide → sl_coherence → sl_assemble → sl_verify_figures →
sl_compile → sl_emit`` chain with stubbed agentic nodes so the test does
NOT need a real LLM. Asserts:

- state transitions (``report_paper_briefs`` populated, ``report_outline``
  set, ``report_rendered_slides`` populated with the expected count + the
  right ``pattern_kind`` distribution);
- the assembled tex contains the ``% BEGIN paperhub:paper_newcommands``
  block (T4 plumbing reachable via T5 wiring) AND the per-slide frames
  in order;
- ``tool_calls`` rows are recorded for ``report:paper_brief`` (one per
  paper), ``report:plan_deck`` (one), ``report:render_slide`` (one per
  planned slide), ``report:coherence``, ``report:assemble``,
  ``report:verify_figures``, ``report:compile``, ``report:emit`` — the
  full new agent flow reconstructable from the DB alone (per the
  agent-flow observability rule);
- a render error propagates without a silent fallback (negative path).
"""
from __future__ import annotations

import json
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
from paperhub.pipelines.slide_pipeline import compile as compile_mod

# ───────────────────────── fixtures + helpers ───────────────────────


def _seed_asset(source_dir: Path, *, fig_id: str = "fig-000") -> None:
    fig_dir = paper_asset_dir(source_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    (fig_dir / f"{fig_id}.png").write_bytes(b"\x89PNG\r\n")
    write_paper_asset(
        PaperAsset(
            figures=[
                FigureAsset(
                    id=fig_id,
                    caption="The architecture diagram.",
                    page=1,
                    section="Method",
                    image_path=f"figures/{fig_id}.png",
                )
            ],
            sections=[SectionAsset(name="Method", order=0)],
        ),
        source_dir,
    )


async def _seed_two_papers(migrated_db: Any, tmp_path: Path) -> tuple[Path, Path]:
    """Insert two paper_content rows + their PaperAsset, both enabled in session 1."""
    source_a = tmp_path / "papA" / "source"
    source_b = tmp_path / "papB" / "source"
    _seed_asset(source_a)
    _seed_asset(source_b)
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) VALUES (?,?,?,?,?,?,?,?)",
        (
            "arxiv:1", "arxiv", "2403.01", "Paper A", "Abstract A",
            "p", str(source_a), "h",
        ),
    )
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) VALUES (?,?,?,?,?,?,?,?)",
        (
            "arxiv:2", "arxiv", "2403.02", "Paper B", "Abstract B",
            "p", str(source_b), "h",
        ),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1,1,1)"
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1,2,1)"
    )
    await migrated_db.commit()
    return source_a, source_b


class _NullAdapter:
    """The T5 chain does not call the LlmAdapter for the deprecated
    ``PaperBrief`` / ``TalkOutline`` / ``FrameDraft`` schemas anymore (each
    agentic node owns its own litellm call). It DOES call the adapter for
    (a) ``TargetLanguage`` in ``_resolve`` via ``detect_slide_language``
    and (b) ``slides_coherence/v1`` via ``coherence_pass`` (streamed).
    Other ``response_model`` types signal an OLD-chain leak and surface
    immediately."""

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is TargetLanguage:
            return TargetLanguage(language=None)
        raise AssertionError(
            f"_NullAdapter.structured got an unexpected response_model "
            f"under the T5 chain: {response_model!r}"
        )

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_coherence/v1":
                yield kw["variables"]["frames_block"]

        return g()


def _deps(adapter, fake_tracer, migrated_db, tmp_path) -> ReportDeps:  # type: ignore[no-untyped-def]
    return ReportDeps(
        adapter=adapter,
        tracer=fake_tracer,
        conn=migrated_db,
        retriever=None,
        workspace=tmp_path,
        plan_model="m",
        section_model="m",
        notes_model="m",
        resolve_model="m",
        recall_enabled=False,
    )


def _state() -> dict[str, Any]:
    return {
        "run_id": 0,
        "branch": "",
        "session_id": 1,
        "user_message": "make a deck for these two papers",
        "effective_query": "make a deck for these two papers",
        "response_language": "English",
        "routing_decision": RoutingDecision(
            intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"
        ),
    }


def _brief(paper_id: int) -> PaperTalkBrief:
    """Hand-authored brief: one figure, one equation, one quantified result.

    ``paper_newcommands`` carries a unique macro per paper so the assemble
    plumbing can be observed end-to-end (T4 plumbing → tex output).
    """
    return PaperTalkBrief(
        paper_id=paper_id,
        contribution=f"Paper {paper_id} contributes X.",
        method_core=f"Paper {paper_id} introduces Y via Z.",
        key_results=[
            KeyResult(
                description=f"Better thing for paper {paper_id}",
                number="14%",
                benchmark="LIBERO",
            )
        ],
        key_figures=[
            KeyFigure(
                key=f"p{paper_id - 1}-fig-000",
                role="overview",
                one_line_interpretation=f"Figure shows pipeline {paper_id}.",
            )
        ],
        key_equations=[
            KeyEquation(
                latex=rf"\mathcal{{L}}_{paper_id} = \sum_i x_i^2",
                role="loss",
                notation_explanation=(
                    f"L_{paper_id} is the loss for paper {paper_id}."
                ),
            )
        ],
        # Letter-only macro names (the extractor's [A-Za-z@]+ stops at the
        # first digit) so paper 1's \MacroOne and paper 2's \MacroTwo are
        # distinct names — exercises the per-paper-merge codepath, not the
        # collision-dedup path.
        paper_newcommands=(
            rf"\newcommand{{\Macro{['One','Two','Three','Four','Five'][paper_id - 1]}}}"
            rf"{{\mathbb{{R}}^{{{paper_id}}}}}"
        ),
        talk_shape_hint="concept+math",
    )


def _outline(briefs: list[PaperTalkBrief]) -> DeckOutline:
    """A small mixed-pattern outline: title + bottlenecks_table + per-paper
    (concept_2col + math_stack) + takeaway_closer.

    Designed to exercise cross-paper patterns (title / bottlenecks_table /
    takeaway_closer carry no ``paper_id``) AND per-paper patterns
    (concept_2col / math_stack attribute to a paper). references slide is
    skipped to keep the test compact — covered by other tests.
    """
    slides = [
        PlannedSlide(
            pattern_kind="title",
            title="",
            goal="Open the talk.",
            paper_id=None,
            figure_key=None,
            equation_index=None,
            key_points=[],
            chunk_ids=[],
        ),
        PlannedSlide(
            pattern_kind="bottlenecks_table",
            title="Three bottlenecks",
            goal="Frame the three papers as one problem.",
            paper_id=None,
            figure_key=None,
            equation_index=None,
            key_points=["B1", "B2", "B3"],
            chunk_ids=[],
        ),
        PlannedSlide(
            pattern_kind="concept_2col",
            title="Paper 1 concept",
            goal="Pitch paper 1.",
            paper_id=briefs[0].paper_id,
            figure_key=briefs[0].key_figures[0].key,
            equation_index=None,
            key_points=["k1"],
            chunk_ids=[],
        ),
        PlannedSlide(
            pattern_kind="math_stack",
            title="Paper 1 math",
            goal="Show the loss.",
            paper_id=briefs[0].paper_id,
            figure_key=None,
            equation_index=0,
            key_points=[],
            chunk_ids=[],
        ),
        PlannedSlide(
            pattern_kind="concept_2col",
            title="Paper 2 concept",
            goal="Pitch paper 2.",
            paper_id=briefs[1].paper_id,
            figure_key=briefs[1].key_figures[0].key,
            equation_index=None,
            key_points=["k1"],
            chunk_ids=[],
        ),
        PlannedSlide(
            pattern_kind="math_stack",
            title="Paper 2 math",
            goal="Show the loss.",
            paper_id=briefs[1].paper_id,
            figure_key=None,
            equation_index=0,
            key_points=[],
            chunk_ids=[],
        ),
        PlannedSlide(
            pattern_kind="takeaway_closer",
            title="",
            goal="Close the talk.",
            paper_id=None,
            figure_key=None,
            equation_index=None,
            key_points=[],
            chunk_ids=[],
        ),
    ]
    return DeckOutline(
        talk_title="A Test Talk",
        talk_subtitle=None,
        slides=slides,
        style_profile_name="default",
    )


def _frame_tex_for(planned: PlannedSlide) -> str:
    """Hand-authored frame text for each pattern_kind, matching T3's
    deterministic-validation contracts (one \\begin{frame}/\\end{frame};
    title carries [plain]+\\titlepage; takeaway_closer has no \\frametitle;
    math_stack carries a display-math block; figure-carrying patterns
    include a \\includegraphics for the assigned key)."""
    if planned.pattern_kind == "title":
        return "\\begin{frame}[plain]\n  \\titlepage\n\\end{frame}"
    if planned.pattern_kind == "takeaway_closer":
        return (
            "\\begin{frame}[plain]\n  Take-away. Thank you.\n\\end{frame}"
        )
    if planned.pattern_kind == "math_stack":
        return (
            "\\begin{frame}{" + planned.title + "}\n"
            "\\[\n  \\mathcal{L} = \\sum_i x_i^2\n\\]\n"
            "\\end{frame}"
        )
    if planned.pattern_kind == "concept_2col":
        key = planned.figure_key or ""
        return (
            "\\begin{frame}{" + planned.title + "}\n"
            "\\includegraphics{" + key + "}\n"
            "\\end{frame}"
        )
    if planned.pattern_kind == "bottlenecks_table":
        return (
            "\\begin{frame}{" + planned.title + "}\n"
            "\\begin{tabular}{lll}A & B & C\\\\\\end{tabular}\n"
            "\\end{frame}"
        )
    return (
        "\\begin{frame}{" + planned.title + "}\nbody\n\\end{frame}"
    )


def _install_stubs(monkeypatch: Any, briefs: list[PaperTalkBrief],
                   outline: DeckOutline) -> dict[str, list[Any]]:
    """Patch the three agentic-brief nodes in ``report_graph``. Returns a
    dict capturing every call so the test can assert input shapes."""
    captured: dict[str, list[Any]] = {
        "paper_brief_calls": [],
        "plan_deck_calls": [],
        "render_slide_calls": [],
    }
    brief_by_id = {b.paper_id: b for b in briefs}

    async def fake_paper_brief(
        *, paper_content_id, paper_idx, title, tracer, model, conn, **kw  # type: ignore[no-untyped-def]
    ):
        captured["paper_brief_calls"].append({
            "paper_content_id": paper_content_id,
            "paper_idx": paper_idx,
            "title": title,
        })
        # Record a real tracer step so the test sees `report:paper_brief`
        # in tool_calls (and so latency_ms etc. get realistic values).
        async with tracer.step(
            agent="report", tool="report:paper_brief", model=model,
        ) as step:
            step.record_args({"paper_content_id": paper_content_id})
            step.record_result({"stubbed": True})
        return brief_by_id[paper_content_id]

    async def fake_plan_deck(
        *, briefs, target_slide_count, talk_title_hint, tracer, model, **kw  # type: ignore[no-untyped-def]
    ):
        captured["plan_deck_calls"].append({
            "brief_ids": [b.paper_id for b in briefs],
            "target_slide_count": target_slide_count,
        })
        async with tracer.step(
            agent="report", tool="report:plan_deck", model=model,
        ) as step:
            step.record_args({"target": target_slide_count})
            step.record_result({
                "stubbed": True,
                "planned_slides_count": len(outline.slides),
            })
        return outline

    async def fake_render_slide(
        *, planned_slide, deck_outline, paper_brief, all_briefs,
        tracer, model, **kw,  # type: ignore[no-untyped-def]
    ):
        captured["render_slide_calls"].append({
            "pattern_kind": planned_slide.pattern_kind,
            "paper_id": planned_slide.paper_id,
            "has_paper_brief": paper_brief is not None,
            "all_briefs_count": len(all_briefs),
        })
        slide_idx = next(
            i for i, s in enumerate(deck_outline.slides) if s is planned_slide
        )
        frame_tex = _frame_tex_for(planned_slide)
        # Mirror T3's bidirectional figure-key tracking so the rendered
        # slide passes validation (the validator runs on the real
        # RenderedSlide but here we hand-craft it).
        figure_keys = (
            [planned_slide.figure_key]
            if planned_slide.figure_key
            else []
        )
        async with tracer.step(
            agent="report", tool="report:render_slide", model=model,
        ) as step:
            step.record_args({
                "slide_index": slide_idx,
                "pattern_kind": planned_slide.pattern_kind,
                "paper_id": planned_slide.paper_id,
            })
            step.record_result({
                "stubbed": True,
                "frame_tex_first_200_chars": frame_tex[:200],
                "figure_keys_used": figure_keys,
            })
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


def _install_fake_compile(monkeypatch: Any, page_count: int = 7) -> dict[str, str]:
    """Stub the pdflatex compile step (the real one needs a working LaTeX
    distribution + the assembled tex to actually compile). The fake compile
    writes the tex + a sentinel PDF and returns success."""
    captured: dict[str, str] = {}

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / tex_name).write_text(tex, encoding="utf-8")  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        captured["tex"] = tex
        return compile_mod.CompileResult(True, 1, tex, "", page_count)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)
    return captured


# ─────────────────────── happy-path integration ──────────────────────


@pytest.mark.asyncio
async def test_round1_chain_end_to_end(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """Drive the full new chain: 2 papers in → 7 rendered slides + assemble
    + verify_figures + compile + emit. Assert every contract."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    await _seed_two_papers(migrated_db, tmp_path)
    briefs = [_brief(paper_id=1), _brief(paper_id=2)]
    outline = _outline(briefs)
    stub_calls = _install_stubs(monkeypatch, briefs, outline)
    compile_captured = _install_fake_compile(monkeypatch, page_count=7)

    deps = _deps(_NullAdapter(), fake_tracer, migrated_db, tmp_path)
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

    # ---- stub-call shape (sanity: every node fired in the right order) ----
    assert len(stub_calls["paper_brief_calls"]) == 2, (
        "sl_paper_brief must fan out once per enabled paper"
    )
    # Per-paper attribution: ids and idx must be (1,0) + (2,1) — the
    # paper_idx tells T1 which "p{idx}-" prefix is theirs.
    pb = sorted(stub_calls["paper_brief_calls"], key=lambda c: c["paper_idx"])
    assert pb[0]["paper_content_id"] == 1 and pb[0]["paper_idx"] == 0
    assert pb[1]["paper_content_id"] == 2 and pb[1]["paper_idx"] == 1

    assert len(stub_calls["plan_deck_calls"]) == 1
    assert stub_calls["plan_deck_calls"][0]["brief_ids"] == [1, 2]

    assert len(stub_calls["render_slide_calls"]) == len(outline.slides)
    rendered_patterns = [
        c["pattern_kind"] for c in stub_calls["render_slide_calls"]
    ]
    assert rendered_patterns.count("title") == 1
    assert rendered_patterns.count("bottlenecks_table") == 1
    assert rendered_patterns.count("concept_2col") == 2
    assert rendered_patterns.count("math_stack") == 2
    assert rendered_patterns.count("takeaway_closer") == 1
    # Cross-paper patterns must NOT carry a paper_brief; per-paper patterns MUST.
    for c in stub_calls["render_slide_calls"]:
        if c["pattern_kind"] in {"title", "bottlenecks_table", "takeaway_closer"}:
            assert c["has_paper_brief"] is False, (
                f"cross-paper pattern {c['pattern_kind']} got a paper_brief"
            )
        else:
            assert c["has_paper_brief"] is True
        # all_briefs is always the full list so cross-paper patterns can see
        # every brief (references / bottlenecks_table need this).
        assert c["all_briefs_count"] == 2

    # ---- state slots populated by the new chain ----
    assert len(last_values.get("report_paper_briefs", [])) == 2
    assert {b.paper_id for b in last_values["report_paper_briefs"]} == {1, 2}
    assert isinstance(last_values.get("report_outline"), DeckOutline)
    assert last_values["report_outline"].talk_title == "A Test Talk"
    rendered = last_values.get("report_rendered_slides", [])
    assert len(rendered) == len(outline.slides)
    assert [r.pattern_kind for r in rendered] == [
        s.pattern_kind for s in outline.slides
    ]

    # ---- assembled tex contains the T4 plumbing block + per-slide frames ----
    tex = compile_captured["tex"]
    assert "% BEGIN paperhub:paper_newcommands" in tex
    assert "% END paperhub:paper_newcommands" in tex
    # Per-paper macros plumbed through as \providecommand (A2 option 1 fix).
    assert r"\providecommand{\MacroOne}{\mathbb{R}^{1}}" in tex
    assert r"\providecommand{\MacroTwo}{\mathbb{R}^{2}}" in tex
    # Per-slide frames appear IN ORDER.
    frame_positions = []
    for s in outline.slides:
        snippet = _frame_tex_for(s)
        # The first \begin{frame} of the deck is the assemble-injected
        # \titlepage frame (T3 hand-render is identical to that), so the
        # title-slide snippet may match the assemble injection. Use the
        # frametitle (or \[ for math_stack, "Take-away" for closer) as a
        # disambiguator instead.
        if s.pattern_kind == "title":
            continue  # assemble already prepends an identical title frame
        marker = (
            "\\frametitle{" + s.title + "}"
            if "\\frametitle" in snippet
            else snippet.splitlines()[1][:20]
        )
        pos = tex.find(snippet)
        if pos == -1:
            # Fall back to the marker — coherence may have whitespace-collapsed
            # the frame slightly via the echo-stream stub.
            pos = tex.find(marker)
        assert pos != -1, f"frame for {s.pattern_kind} not in deck tex"
        frame_positions.append(pos)
    assert frame_positions == sorted(frame_positions), (
        f"slide frames must appear in planned order, got {frame_positions}"
    )

    # ---- the deck event was emitted with the new talk_title ----
    deck_evt = next(e for e in events if e.get("event") == "deck")
    assert deck_evt["deck"]["title"] == "A Test Talk"
    assert deck_evt["deck"]["page_count"] == 7

    # ---- tool_calls trace: every new + reused stage recorded ----
    async with migrated_db.execute(
        "SELECT tool, COUNT(*) FROM tool_calls "
        "WHERE run_id = ? GROUP BY tool",
        (fake_tracer.run_id,),
    ) as cur:
        counts = {row[0]: row[1] for row in await cur.fetchall()}
    # New chain stages — one per call site.
    assert counts.get("report:paper_brief", 0) == 2, counts
    assert counts.get("report:plan_deck", 0) == 1, counts
    assert counts.get("report:render_slide", 0) == len(outline.slides), counts
    # Reused stages.
    assert counts.get("report:coherence", 0) == 1, counts
    assert counts.get("report:assemble", 0) == 1, counts
    assert counts.get("report:verify_figures", 0) == 1, counts
    assert counts.get("report:compile", 0) == 1, counts
    assert counts.get("report:emit", 0) == 1, counts
    # The deprecated chain MUST NOT fire.
    assert counts.get("report:understand", 0) == 0, counts
    assert counts.get("report:narrate", 0) == 0, counts
    assert counts.get("report:draft", 0) == 0, counts

    # ---- assemble step records the T4 newcommands summary ----
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls "
        "WHERE run_id = ? AND tool = 'report:assemble'",
        (fake_tracer.run_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    asm = json.loads(row[0])
    assert asm["newcommands_unique_count"] == 2
    assert asm["newcommands_contributing_papers"] == 2
    assert asm["newcommands_collisions"] == []

    # ---- final state: deck on disk + DB row ----
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None and deck.status == "ok"
    assert deck.page_count == 7
    rows = await get_deck_slides(migrated_db, deck_id=deck.id)
    assert len(rows) >= 1


# ─────────────────────── negative path ──────────────────────────────


@pytest.mark.asyncio
async def test_round1_render_error_propagates(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """A render error must NOT be silently swallowed — the subgraph
    propagates the exception (no defensive fallback to an empty deck or a
    paper-omitted frame). Mirrors T3's render_parse_failed contract."""
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    await _seed_two_papers(migrated_db, tmp_path)
    briefs = [_brief(paper_id=1), _brief(paper_id=2)]
    outline = _outline(briefs)
    _install_stubs(monkeypatch, briefs, outline)

    # Wrap the existing stub to raise on the 3rd planned slide
    # (first per-paper concept_2col) so we hit the error inside a fan-out.
    original_render = rg.run_sl_render_slide
    call_count = {"n": 0}

    async def boom(**kw):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("simulated render failure on slide 3")
        return await original_render(**kw)

    monkeypatch.setattr(rg, "run_sl_render_slide", boom)
    _install_fake_compile(monkeypatch)

    deps = _deps(_NullAdapter(), fake_tracer, migrated_db, tmp_path)
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    with pytest.raises(RuntimeError, match="simulated render failure"):
        async for _mode, _payload in graph.astream(
            state, stream_mode=["custom", "values"]
        ):
            pass

    # No deck should have landed in the DB.
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is None, (
        "a failed render must not produce a persisted deck row "
        "(no silent fallback)"
    )
