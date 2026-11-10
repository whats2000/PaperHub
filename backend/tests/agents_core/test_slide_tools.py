"""Slide tools of the core agent (H4 Task 7). The report pipeline is stubbed."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from paperhub.agents.core.tools import ToolOutcome
from paperhub.agents.research import FinalOnlyMessage, ToolStepYield
from paperhub.api import chat as chat_module
from paperhub.api.chat import DeckYield
from paperhub.db.decks import upsert_deck
from paperhub.tracing.tracer import Tracer

from .test_action_tools_basic import _ctx, _run

pytestmark = pytest.mark.asyncio

DECK = {"deck_id": 1, "session_id": 1, "page_count": 12, "title": "My Deck",
        "status": "ok", "contributing_papers": [1], "has_notes": False,
        "version_id": 5}


def _stub(monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]],
          *, deck: bool = True, final: str = "Built.") -> None:
    async def fake(state: Any, **_kw: Any) -> Any:
        seen.append(dict(state))
        yield ToolStepYield(record={"step_index": 1, "tool": "report:x"})
        if deck:
            yield DeckYield(deck=DECK)
        yield FinalOnlyMessage(final)

    monkeypatch.setattr(chat_module, "report_stream", fake)


async def _with_session(conn: aiosqlite.Connection, *, deck: bool) -> None:
    await conn.execute("INSERT OR IGNORE INTO chat_sessions (id) VALUES (1)")
    await conn.commit()
    if deck:
        await upsert_deck(conn, session_id=1, run_id=None, tex_path="/x.tex",
                          pdf_path=None, speaker_notes={}, plan={}, page_count=3,
                          contributing_paper_ids=[], status="ok")


async def test_create_deck_without_deck_generates_and_emits_deck(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=False)
    seen: list[dict[str, Any]] = []
    _stub(monkeypatch, seen)
    ctx = _ctx(migrated_db, fake_tracer, tmp_path)
    out: ToolOutcome = await _run(
        ctx, "create_deck", brief="journal club talk", slides=10, language="English")
    assert [n for n, _ in out.events].count("deck") == 1
    assert seen[0].get("report_forced_command") is None
    assert "journal club talk" in seen[0]["effective_query"]
    assert "10 slides" in seen[0]["effective_query"]
    assert "My Deck" in out.content and "version 5" in out.content
    assert out.record["version_id"] == 5 and out.record["command"] == "generate"


async def test_create_deck_with_existing_deck_forces_regenerate(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=True)
    seen: list[dict[str, Any]] = []
    _stub(monkeypatch, seen)
    out = await _run(_ctx(migrated_db, fake_tracer, tmp_path),
                     "create_deck", brief="again")
    assert seen[0]["report_forced_command"].action == "regenerate"
    assert out.record["command"] == "regenerate"


async def test_edit_slides_pages_forces_target_page(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=True)
    seen: list[dict[str, Any]] = []
    _stub(monkeypatch, seen)
    out = await _run(_ctx(migrated_db, fake_tracer, tmp_path),
                     "edit_slides", instruction="shorten", pages=[3])
    cmd = seen[0]["report_forced_command"]
    assert (cmd.action, cmd.target_scope, cmd.target_page) == ("edit_slides", "page", 3)
    assert seen[0]["effective_query"] == "shorten"
    assert out.record["pages"] == [{"page": 3, "version_id": 5, "ok": True}]


async def test_write_notes_forces_generate_notes(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=True)
    seen: list[dict[str, Any]] = []
    _stub(monkeypatch, seen)
    await _run(_ctx(migrated_db, fake_tracer, tmp_path),
               "write_notes", language="Japanese")
    cmd = seen[0]["report_forced_command"]
    assert cmd.action == "generate_notes" and cmd.note_language == "Japanese"


async def test_edit_without_deck_is_readable_error(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=False)
    seen: list[dict[str, Any]] = []
    _stub(monkeypatch, seen)
    out = await _run(_ctx(migrated_db, fake_tracer, tmp_path),
                     "edit_slides", instruction="x")
    assert "create_deck" in out.content and not seen


async def test_no_deck_produced_surfaces_pipeline_message(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=False)
    _stub(monkeypatch, [], deck=False, final="Install a LaTeX distribution.")
    out = await _run(_ctx(migrated_db, fake_tracer, tmp_path),
                     "create_deck", brief="b")
    assert "LaTeX" in out.content and all(n != "deck" for n, _ in out.events)


async def test_edit_slides_multi_page_loops_one_forced_command_per_page(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=True)
    seen: list[dict[str, Any]] = []
    _stub(monkeypatch, seen)
    out = await _run(_ctx(migrated_db, fake_tracer, tmp_path),
                     "edit_slides", instruction="shorten", pages=[2, 4])
    cmds = [s["report_forced_command"] for s in seen]
    assert [(c.target_scope, c.target_page) for c in cmds] == [("page", 2), ("page", 4)]
    assert out.record["pages"] == [
        {"page": 2, "version_id": 5, "ok": True},
        {"page": 4, "version_id": 5, "ok": True},
    ]
    assert [n for n, _ in out.events].count("deck") == 2


async def test_edit_slides_multi_page_stops_at_first_failure(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _with_session(migrated_db, deck=True)
    seen: list[dict[str, Any]] = []

    async def fake(state: Any, **_kw: Any) -> Any:
        seen.append(dict(state))
        yield FinalOnlyMessage("compile failed")  # no deck event

    monkeypatch.setattr(chat_module, "report_stream", fake)
    out = await _run(_ctx(migrated_db, fake_tracer, tmp_path),
                     "edit_slides", instruction="x", pages=[2, 4, 6])
    assert len(seen) == 1
    assert out.record["pages"] == [{"page": 2, "version_id": None, "ok": False}]
    assert "page 2" in out.content and "not" in out.content.lower()
