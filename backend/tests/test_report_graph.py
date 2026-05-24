from pathlib import Path
from typing import Any

import pytest

from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
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
