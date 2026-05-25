from pathlib import Path
from typing import Any

import pytest

from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.agents.report_pipeline import NoteSegments
from paperhub.db.decks import get_deck
from paperhub.models.domain import (
    OutlineSlide,
    PaperBrief,
    RoutingDecision,
    SlideDraft,
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
    """Stub adapter for the F3 PhD flow.

    structured() dispatches on response_model: a PaperBrief for understand,
    a 2-slide TalkOutline for narrate, a SlideDraft for each draft.
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
        if response_model is SlideDraft:
            self._draft_calls += 1
            if self._draft_calls == 1:
                return SlideDraft(
                    frame=(
                        "\\begin{frame}{Motivation}"
                        "\\includegraphics{p0-fig-000}\\end{frame}"
                    ),
                    note="Explain the motivation in depth.",
                )
            return SlideDraft(
                frame="\\begin{frame}{Method}\\includegraphics{ghost}\\end{frame}",
                note="Walk through the method.",
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
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(payload)

    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    assert deck.status == "ok"
    assert deck.page_count > 0
    assert any(e.get("event") == "deck" for e in events)
    # The assigned (real) figure was staged under its deck-unique key.
    figures_dir = tmp_path / "chat_session" / "1" / "slides" / "figures"
    assert (figures_dir / "p0-fig-000.png").exists()
    assert (tmp_path / "chat_session" / "1" / "slides" / "deck.pdf").exists()


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
# _SplitAdapter — a minimal variant of _Adapter that also handles
# NoteSegments for the slides_note_split/v1 slot (F3 T9 split-frame path).
#
# The outline returns ONE content slide ("Method") so there is exactly one
# SlideDraft.  The stub compile tex contains \maketitle + two frames that
# share the same \frametitle{Method} (a logical split across 2 PDF pages).
# With page_count=3 (title + 2 split pages) finalize_notes groups pages [2,3]
# as one multi-page content group and calls the split LLM with NoteSegments.
# ---------------------------------------------------------------------------
class _SplitAdapter:
    """Stub adapter for the split-note integration test.

    structured() dispatches on response_model:
      - PaperBrief   → a one-paper brief
      - TalkOutline  → one content slide titled "Method"
      - SlideDraft   → one frame+note for "Method"
      - NoteSegments → two distinct per-page segments for the split frame
    """

    def __init__(self) -> None:
        self.note_split_calls: int = 0

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
        if response_model is SlideDraft:
            return SlideDraft(
                frame="\\begin{frame}{Method}\\includegraphics{p0-fig-000}\\end{frame}",
                note="Full method note covering both pages.",
            )
        if response_model is NoteSegments:
            self.note_split_calls += 1
            return NoteSegments(segments=["page two note", "page three note"])
        raise AssertionError(f"unexpected response_model {response_model!r}")

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_coherence/v1":
                yield kw["variables"]["frames_block"]

        return g()


# Tex returned by the stub compile: \maketitle (page 1) + two consecutive
# \begin{frame}{Method}...\end{frame} blocks (pages 2 and 3 — a logical
# split slide).  finalize_notes sees groups [[1], [2, 3]] where group [1]
# is a title page and group [2, 3] is the split content group → triggers
# the slides_note_split/v1 LLM call.
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
async def test_split_frame_gets_per_page_notes(  # type: ignore[no-untyped-def]
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    """Graph-level integration test: a compile result that splits one logical
    slide across two frames (same \\frametitle{Method}) produces DISTINCT
    per-page speaker notes — NOT '(continued)' — via the note-split LLM."""
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

    # ---- verify the note-split LLM path was exercised ----
    assert adapter.note_split_calls == 1, (
        "expected exactly one slides_note_split/v1 call for the 2-page group"
    )

    # ---- verify the deck row was written correctly ----
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    assert deck.status == "ok"
    assert deck.page_count == 3

    notes: dict[str, str] = deck.speaker_notes

    # All three pages must be present.
    assert set(notes.keys()) == {"1", "2", "3"}, f"unexpected note keys: {notes.keys()}"

    # Page 1 is the title page — note is empty (never "(continued)").
    assert notes["1"] != "(continued)", "title page must not be '(continued)'"

    # Pages 2 and 3 must be the distinct segments returned by _SplitAdapter.
    assert notes["2"] == "page two note", f"page 2 note: {notes['2']!r}"
    assert notes["3"] == "page three note", f"page 3 note: {notes['3']!r}"

    # Sanity: no note value equals "(continued)" anywhere.
    for page_key, note_text in notes.items():
        assert note_text != "(continued)", (
            f"page {page_key} has '(continued)' — split path failed"
        )
