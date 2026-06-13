"""Tests for the sl_qa node and _route_deck_command routing logic (v2.29).

Adaptations from the task spec:
- ``test_sl_qa_delegates_to_answer_callback``: constructs a Tracer directly
  from the same ``conn`` (not via ``fake_tracer`` fixture) because ``fake_tracer``
  is bound to a separate ``migrated_db`` connection — the two DBs have disjoint
  run-id spaces, so using the fixture's run_id in the test's conn would fail FK
  lookups. A real ``runs`` row is inserted into the test's conn instead.
- ``classify_deck_command`` and ``detect_slide_language`` are monkeypatched to
  return deterministic values because ``_resolve`` ALWAYS re-classifies when a
  deck exists (it does not respect a pre-set ``report_command`` in state). With
  ``adapter=object()`` the real functions would crash.
- ``_pdflatex_available`` is also monkeypatched so the route tests run reliably
  in CI (no pdflatex required for qa routing).
"""

from paperhub.models.domain import DeckCommand


def test_route_qa_goes_to_sl_qa(monkeypatch) -> None:
    from paperhub.agents import report_graph as rg
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)
    state = {"report_papers": [{"id": 1}], "report_command": DeckCommand(action="qa", target_page=None)}
    assert rg._route_deck_command(state) == "qa"


def test_route_unknown_action_never_edits(monkeypatch) -> None:
    from paperhub.agents import report_graph as rg
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)
    assert rg._route_deck_command(
        {"report_papers": [{"id": 1}], "report_command": DeckCommand(action="edit_slides", target_page=None)}
    ) == "edit_slides"
    assert rg._route_deck_command(
        {"report_papers": [{"id": 1}], "report_command": DeckCommand(action="qa", target_page=None)}
    ) == "qa"


def test_route_qa_answered_even_without_latex(monkeypatch) -> None:
    from paperhub.agents import report_graph as rg
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: False)
    assert rg._route_deck_command(
        {"report_papers": [{"id": 1}], "report_command": DeckCommand(action="qa", target_page=None)}
    ) == "qa"


async def test_sl_qa_delegates_to_answer_callback(monkeypatch, tmp_path) -> None:
    from pathlib import Path

    from paperhub.agents import report_graph as rg
    from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
    from paperhub.db.connection import open_db
    from paperhub.db.deck_slides import (
        DeckSlideInput,
        get_deck_slides,
        replace_deck_slides,
    )
    from paperhub.db.decks import get_deck, upsert_deck
    from paperhub.db.migrate import apply_schema
    from paperhub.tracing.tracer import Tracer

    # Monkeypatch _resolve's two LLM calls so adapter=object() doesn't crash.
    # _resolve always re-classifies when a deck exists — it does not short-circuit
    # on a pre-set report_command in state. Both functions run inside asyncio.gather.
    monkeypatch.setattr(
        rg, "classify_deck_command",
        lambda **_kwargs: _async_return(DeckCommand(action="qa", target_page=None)),
    )
    monkeypatch.setattr(
        rg, "detect_slide_language",
        lambda **_kwargs: _async_return(None),
    )
    # Ensure _pdflatex_available doesn't gate the route (qa bypasses it anyway,
    # but _resolve has an early-exit guard: 'if not papers or not _pdflatex_available')
    # that we must also bypass so _resolve actually runs classify_deck_command.
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)

    async def _answer(_state) -> str:
        return "The graph shows X [chunk:101]."

    async with open_db(tmp_path / "t.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        # Insert the run so the Tracer's run_id is valid in this conn.
        await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        run_id = int(row[0])
        # paper_content requires: content_key (UNIQUE NOT NULL), kind, title,
        # source_path, source_dir_path, html_path, and exactly one of arxiv_id/sha256
        # (enforced by CHECK constraint).
        await conn.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, title, source_path, source_dir_path, html_path, arxiv_id) "
            "VALUES (7, 'arxiv:test-p7', 'arxiv', 'P', '/x/source.tex', '/x', '/x/source.html', 'test-p7')"
        )
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 7, 1)"
        )
        await conn.commit()
        await upsert_deck(conn, session_id=1, run_id=None, tex_path="/x.tex",
                          pdf_path=None, speaker_notes={}, plan={}, page_count=1,
                          contributing_paper_ids=[], status="ok")
        deck = await get_deck(conn, session_id=1)
        assert deck is not None
        await replace_deck_slides(conn, deck_id=deck.id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="\\begin{frame}{A}b\\end{frame}",
                           page_start=1, page_end=1)])

        tracer = Tracer(conn, run_id=run_id, branch="")
        deps = ReportDeps(
            adapter=object(), tracer=tracer, conn=conn, workspace=Path(tmp_path),
            plan_model="m", section_model="m", notes_model="m", resolve_model="m",
            answer_slide_question=_answer)
        graph = build_report_subgraph(deps)
        state = {"run_id": run_id, "branch": "", "session_id": 1,
                 "user_message": "explain this graph", "current_view_page": 1,
                 "report_command": DeckCommand(action="qa", target_page=None),
                 "report_papers": [{"id": 7, "source_dir": "/x"}]}
        final = ""
        async for mode, payload in graph.astream(state, stream_mode=["values"]):
            if mode == "values" and isinstance(payload, dict) and "final_response" in payload:
                final = payload["final_response"]
        assert final == "The graph shows X [chunk:101]."
        rows = await get_deck_slides(conn, deck_id=deck.id)
        assert rows[0].frame_tex == "\\begin{frame}{A}b\\end{frame}"  # untouched


def test_route_genuinely_unknown_action_falls_through_to_qa(monkeypatch) -> None:
    from types import SimpleNamespace

    from paperhub.agents import report_graph as rg
    monkeypatch.setattr(rg, "_pdflatex_available", lambda: True)
    state = {"report_papers": [{"id": 1}],
             "report_command": SimpleNamespace(action="some_future_action")}
    assert rg._route_deck_command(state) == "qa"  # NEVER "edit_slides"


async def _async_return(val):
    return val
