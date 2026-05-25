from pathlib import Path
from typing import Any

import pytest

from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.db.deck_slides import get_deck_slides
from paperhub.db.decks import get_deck
from paperhub.models.domain import (
    FrameDraft,
    OutlineSlide,
    PaperBrief,
    RoutingDecision,
    TalkOutline,
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


class _Adapter:
    """Stub adapter for the F3/F4 PhD flow.

    structured() dispatches on response_model: a PaperBrief for understand,
    a 2-slide TalkOutline for narrate, a FrameDraft for each draft (F4:
    frame-only, no note).
    The first draft references the staged inventory figure ("p0-fig-000");
    the second draft references a non-inventory key ("ghost") to drive the
    deterministic no-hallucination guard.
    """

    def __init__(self) -> None:
        self._draft_calls = 0

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is PaperBrief:
            return PaperBrief(
                paper_id=1,
                contribution="A new mechanism.",
                method="Scaled attention.",
                key_results=["SOTA"],
                key_figure_keys=["p0-fig-000"],
                key_equations=["E=mc^2"],
            )
        if response_model is TalkOutline:
            return TalkOutline(
                title="MoE",
                slides=[
                    OutlineSlide(
                        title="Motivation",
                        goal="why",
                        key_points=["a"],
                        figure_key="p0-fig-000",
                        paper_ids=[1],
                    ),
                    OutlineSlide(
                        title="Method",
                        goal="how",
                        key_points=["b"],
                        figure_key="ghost",
                        paper_ids=[1],
                    ),
                ],
            )
        if response_model is FrameDraft:
            self._draft_calls += 1
            if self._draft_calls == 1:
                return FrameDraft(
                    frame=(
                        "\\begin{frame}{Motivation}"
                        "\\includegraphics{p0-fig-000}\\end{frame}"
                    ),
                )
            return FrameDraft(
                frame="\\begin{frame}{Method}\\includegraphics{ghost}\\end{frame}",
            )
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_coherence/v1":
                # Echo the input frames back unchanged.
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


@pytest.mark.asyncio
async def test_create_deck_happy_path(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    source_dir = tmp_path / "cacheA" / "source"
    _seed_asset(source_dir)
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1', 'arxiv', '2403.01', 'Paper A', 'An abstract.', 'p', ?, 'h')",
        (str(source_dir),),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)"
    )
    await migrated_db.commit()

    captured: dict[str, str] = {}

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        captured["tex"] = tex
        return compile_mod.CompileResult(True, 1, tex, "", 2)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    class _Retr:
        def retrieve(self, q, *, enabled_paper_content_ids, corpus_size, top_k=10):  # type: ignore[no-untyped-def]
            return []

    deps = _make_deps(_Adapter(), fake_tracer, migrated_db, _Retr(), tmp_path)
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
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1', 'arxiv', '2403.01', 'Paper A', 'An abstract.', 'p', ?, 'h')",
        (str(source_dir),),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)"
    )
    await migrated_db.commit()

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, tex, "", 2)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    class _Retr:
        def retrieve(self, q, *, enabled_paper_content_ids, corpus_size, top_k=10):  # type: ignore[no-untyped-def]
            return []

    deps = _make_deps(_Adapter(), fake_tracer, migrated_db, _Retr(), tmp_path)
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

    # The per-stage stages are represented (understand/narrate/draft tool names).
    tool_names = {r["tool"] for r in tool_steps}
    assert {"report:understand", "report:narrate", "report:draft"} & tool_names, (
        f"expected per-stage tool names, got {tool_names}"
    )

    # No record is emitted twice (dedupe by step_index).
    step_indices = [r["step_index"] for r in tool_steps]
    assert len(step_indices) == len(set(step_indices)), "duplicate tool_step emitted"


@pytest.mark.asyncio
async def test_no_hallucination_unknown_figure_neutralized(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "paperhub.agents.report_graph._pdflatex_available", lambda: True
    )
    source_dir = tmp_path / "cacheA" / "source"
    _seed_asset(source_dir)
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1', 'arxiv', '2403.01', 'Paper A', 'An abstract.', 'p', ?, 'h')",
        (str(source_dir),),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)"
    )
    await migrated_db.commit()

    captured: dict[str, str] = {}

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        captured["tex"] = tex
        return compile_mod.CompileResult(True, 1, tex, "", 2)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    deps = _make_deps(_Adapter(), fake_tracer, migrated_db, None, tmp_path)
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
    deps = _make_deps(_Adapter(), fake_tracer, migrated_db, None, tmp_path)
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
    deps = _make_deps(_Adapter(), fake_tracer, migrated_db, None, tmp_path)
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
# _SplitAdapter — a minimal variant of _Adapter for the split-frame path.
#
# The outline returns ONE content slide ("Method"). The stub compile tex
# contains \maketitle + two frames that share the same \frametitle{Method}
# (a logical split across 2 PDF pages, page_count=3). The GENERATE path does
# NOT author speaker notes (notes are an opt-in F4 sub-flow).
# ---------------------------------------------------------------------------
class _SplitAdapter:
    """Stub adapter for the split-frame integration test (F4: slides-only path).

    structured() dispatches on response_model:
      - PaperBrief   → a one-paper brief
      - TalkOutline  → one content slide titled "Method"
      - FrameDraft   → one frame-only draft for "Method" (no note field)
    """

    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        if response_model is PaperBrief:
            return PaperBrief(
                paper_id=1,
                contribution="A gradient-based method.",
                method="Backprop.",
                key_results=["SOTA"],
                key_figure_keys=["p0-fig-000"],
                key_equations=[],
            )
        if response_model is TalkOutline:
            return TalkOutline(
                title="Method",
                slides=[
                    OutlineSlide(
                        title="Method",
                        goal="explain the method",
                        key_points=["step 1", "step 2"],
                        figure_key="p0-fig-000",
                        paper_ids=[1],
                    ),
                ],
            )
        if response_model is FrameDraft:
            return FrameDraft(
                frame="\\begin{frame}{Method}\\includegraphics{p0-fig-000}\\end{frame}",
            )
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_coherence/v1":
                yield kw["variables"]["frames_block"]

        return g()


# Tex returned by the stub compile: \maketitle (page 1) + two consecutive
# \begin{frame}{Method}...\end{frame} blocks (pages 2 and 3 — a logical
# split slide across same-frametitle content pages).
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
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:2', 'arxiv', '2403.02', 'Paper B', 'An abstract.', 'p', ?, 'h')",
        (str(source_dir),),
    )
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)"
    )
    await migrated_db.commit()

    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    # Return a 3-page CompileResult: title page + 2 same-frametitle "Method" frames.
    async def fake_split_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(_SPLIT_TEX)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, _SPLIT_TEX, "", 3)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_split_compile)

    adapter = _SplitAdapter()

    class _Retr:
        def retrieve(self, q, *, enabled_paper_content_ids, corpus_size, top_k=10):  # type: ignore[no-untyped-def]
            return []

    deps = _make_deps(adapter, fake_tracer, migrated_db, _Retr(), tmp_path)
    graph = build_report_subgraph(deps)
    state = _state()
    state["run_id"] = fake_tracer.run_id

    async for _mode, _payload in graph.astream(state, stream_mode=["custom", "values"]):
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
