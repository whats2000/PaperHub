from pathlib import Path
from typing import Any

import pytest

from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.db.decks import get_deck
from paperhub.models.domain import PlannedSection, RoutingDecision, SlidePlan


class _Adapter:
    async def structured(self, *, response_model, **kw):  # type: ignore[no-untyped-def]
        return SlidePlan(title="MoE", sections=[
            PlannedSection(title="Motivation", intent="why", paper_content_ids=[1]),
        ])

    def stream(self, *, slot, **kw):  # type: ignore[no-untyped-def]
        async def g():  # type: ignore[no-untyped-def]
            if slot == "slides_section/v1":
                yield "\\begin{frame}{Motivation}\\end{frame}"
            elif slot == "slides_notes/v1":
                yield "[SLIDE 1]\nSay hello."
        return g()


@pytest.mark.asyncio
async def test_create_deck_happy_path(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("paperhub.agents.report_graph._pdflatex_available", lambda: True)
    # one enabled paper
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1', 'arxiv', '2403.01', 'Paper A', 'p', ?, 'h')",
        (str(tmp_path / "cacheA" / "source"),),
    )
    await migrated_db.execute("INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)")
    await migrated_db.commit()

    # fake compile: succeeds, writes a pdf, 1 page
    from paperhub.pipelines.slide_pipeline import compile as compile_mod

    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):  # type: ignore[no-untyped-def]
        Path(workdir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        (Path(workdir) / "deck.tex").write_text(tex)  # noqa: ASYNC240
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")  # noqa: ASYNC240
        return compile_mod.CompileResult(True, 1, tex, "", 1)

    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    # retriever stub
    class _Retr:
        def retrieve(self, q, *, enabled_paper_content_ids, corpus_size, top_k=10):  # type: ignore[no-untyped-def]
            return []

    deps = ReportDeps(
        adapter=_Adapter(), tracer=fake_tracer, conn=migrated_db, retriever=_Retr(),
        workspace=tmp_path, plan_model="m", section_model="m", notes_model="m",
        resolve_model="m", recall_enabled=False,
    )
    graph = build_report_subgraph(deps)
    state: dict[str, Any] = {
        "run_id": fake_tracer.run_id, "branch": "", "session_id": 1,
        "user_message": "make slides", "effective_query": "make slides comparing these",
        "response_language": "English",
        "routing_decision": RoutingDecision(intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"),
    }
    events: list[Any] = []
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(payload)
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None and deck.page_count == 1 and deck.status == "ok"
    assert any(e.get("event") == "deck" for e in events)
    assert (tmp_path / "chat_session" / "1" / "slides" / "deck.pdf").exists()


@pytest.mark.asyncio
async def test_empty_enabled_set_message(fake_tracer, migrated_db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    deps = ReportDeps(
        adapter=_Adapter(), tracer=fake_tracer, conn=migrated_db, retriever=None,
        workspace=tmp_path, plan_model="m", section_model="m", notes_model="m",
        resolve_model="m", recall_enabled=False,
    )
    graph = build_report_subgraph(deps)
    state = {
        "run_id": fake_tracer.run_id, "branch": "", "session_id": 1,
        "user_message": "slides", "effective_query": "slides",
        "routing_decision": RoutingDecision(intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"),
    }
    final = None
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "values" and isinstance(payload, dict) and payload.get("final_response"):
            final = payload["final_response"]
    assert final is not None and "enable" in final.lower()


@pytest.mark.asyncio
async def test_missing_pdflatex_message(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("paperhub.agents.report_graph._pdflatex_available", lambda: False)
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1','arxiv','2403.01','A','p',?,'h')", (str(tmp_path / "s"),))
    await migrated_db.execute("INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1,1,1)")
    await migrated_db.commit()
    deps = ReportDeps(adapter=_Adapter(), tracer=fake_tracer, conn=migrated_db, retriever=None,
                      workspace=tmp_path, plan_model="m", section_model="m", notes_model="m",
                      resolve_model="m", recall_enabled=False)
    graph = build_report_subgraph(deps)
    state: dict[str, Any] = {
        "run_id": fake_tracer.run_id, "branch": "", "session_id": 1,
        "user_message": "slides", "effective_query": "slides",
        "routing_decision": RoutingDecision(
            intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"
        ),
    }
    final = None
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "values" and isinstance(payload, dict) and payload.get("final_response"):
            final = payload["final_response"]
    assert final is not None and "latex" in final.lower()
