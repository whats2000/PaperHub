"""H4: report_forced_command makes _resolve skip classify_deck_command."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from paperhub.models.domain import DeckCommand, SlideMeta

pytestmark = pytest.mark.asyncio


async def _run_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: dict[str, Any],
) -> tuple[str, list[str]]:
    from paperhub.agents import report_graph as rg
    from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
    from paperhub.db.connection import open_db
    from paperhub.db.deck_slides import DeckSlideInput, replace_deck_slides
    from paperhub.db.decks import get_deck, upsert_deck
    from paperhub.db.migrate import apply_schema
    from paperhub.tracing.tracer import Tracer

    calls: list[str] = []

    async def _classify(**_k: Any) -> DeckCommand:
        calls.append("classify")
        return DeckCommand(action="qa", target_page=None, note_language=None)

    async def _meta(**_k: Any) -> SlideMeta:
        calls.append("meta")
        return SlideMeta(language=None, min_pages=None, max_pages=None)

    monkeypatch.setattr(rg, "classify_deck_command", _classify)
    monkeypatch.setattr(rg, "detect_slide_meta", _meta)
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)

    # Route everything to qa so no LLM-backed node runs; report_command is
    # still the thing under test.
    monkeypatch.setattr(rg, "_route_deck_command", lambda _s: "qa")

    async def _answer(_s: Any) -> str:
        return "qa-answer"

    async with open_db(tmp_path / "t.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, title, source_path, "
            "source_dir_path, html_path, arxiv_id) VALUES (7, 'arxiv:t7', 'arxiv', "
            "'P', '/x/s.tex', '/x', '/x/s.html', 't7')")
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 7, 1)")
        await conn.commit()
        await upsert_deck(conn, session_id=1, run_id=None, tex_path="/x.tex",
                          pdf_path=None, speaker_notes={}, plan={}, page_count=1,
                          contributing_paper_ids=[], status="ok")
        deck = await get_deck(conn, session_id=1)
        assert deck is not None
        await replace_deck_slides(conn, deck_id=deck.id, slides=[
            DeckSlideInput(slide_index=0, frame_tex=r"\begin{frame}{A}b\end{frame}",
                           page_start=2, page_end=2)])
        tracer = Tracer(conn, run_id=1, branch="")
        deps = ReportDeps(
            adapter=object(), tracer=tracer, conn=conn, workspace=tmp_path,
            plan_model="m", section_model="m", notes_model="m", resolve_model="m",
            answer_slide_question=_answer)
        graph = build_report_subgraph(deps)
        state: dict[str, Any] = {"run_id": 1, "branch": "", "session_id": 1,
                                 "user_message": "x", "effective_query": "x",
                                 "current_view_page": 1, **extra}
        route = ""
        async for mode, payload in graph.astream(state, stream_mode=["values"]):
            if mode == "values" and isinstance(payload, dict) and "report_command" in payload:
                route = payload["report_command"].action
        return route, calls


async def test_forced_command_skips_classifier(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    forced = DeckCommand(action="edit_title", target_page=None, note_language=None)
    action, calls = await _run_resolve(
        monkeypatch, tmp_path, {"report_forced_command": forced})
    # The stubbed classifier would say "qa"; the forced action must win.
    assert action == "edit_title"
    assert "classify" not in calls and "meta" in calls


async def test_classifier_still_runs_without_forced_command(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    action, calls = await _run_resolve(monkeypatch, tmp_path, {})
    assert action == "qa"
    assert "classify" in calls
