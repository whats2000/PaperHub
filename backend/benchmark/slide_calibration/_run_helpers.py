"""Shared helpers used by run_single.py and run_multi.py."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from benchmark.driver import chat

from ._common import round_dir

# The workspace DB the backend writes to (per CLAUDE.md "Where things live").
WORKSPACE_DB = (
    Path(__file__).resolve().parents[3] / "backend" / "workspace" / "paperhub.db"
)


def _conn() -> sqlite3.Connection:
    if not WORKSPACE_DB.exists():
        raise SystemExit(
            f"workspace DB not found: {WORKSPACE_DB} — is the backend running?"
        )
    return sqlite3.connect(str(WORKSPACE_DB))


def fetch_deck_artifacts(session_id: int) -> dict[str, Any]:
    """Pull the latest deck for a session: tex + pdf path + speaker_notes_json + page_count."""
    con = _conn()
    try:
        row = con.execute(
            "SELECT id, slides_tex, pdf_path, speaker_notes_json, page_count, title, "
            "contributing_papers, has_notes "
            "FROM decks WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    deck_id, tex, pdf_path, notes_json, page_count, title, contributing, has_notes = row
    return {
        "deck_id": deck_id,
        "slides_tex": tex or "",
        "pdf_path": pdf_path,
        "speaker_notes_json": json.loads(notes_json) if notes_json else {},
        "page_count": page_count,
        "title": title,
        "contributing_papers": json.loads(contributing) if contributing else [],
        "has_notes": bool(has_notes),
    }


def fetch_tool_calls(run_id: int) -> list[dict[str, Any]]:
    """Pull the agent-flow trace for the run — args + result for every step."""
    con = _conn()
    try:
        cur = con.execute(
            "SELECT step_index, agent, tool, model, status, latency_ms, "
            "args_redacted_json, result_summary_json, error "
            "FROM tool_calls WHERE run_id = ? ORDER BY step_index",
            (run_id,),
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()
    for r in rows:
        for key in ("args_redacted_json", "result_summary_json"):
            if r.get(key):
                try:
                    r[key] = json.loads(r[key])
                except json.JSONDecodeError:
                    pass
    return rows


def run_and_dump(
    *,
    session_id: int,
    user_message: str,
    round_no: int,
    scenario: str,
    label: str,
    current_view_page: int = 0,
) -> dict[str, Any]:
    """Drive /chat, collect deck artifacts + trace, write everything to disk."""
    out = round_dir(round_no, scenario, label)
    print(
        f"[run] round={round_no} scenario={scenario} label={label} "
        f"session={session_id} → {out}"
    )

    res = chat(session_id, user_message, current_view_page=current_view_page)
    if res.error:
        print(f"[run] ERROR from /chat: {res.error}", flush=True)

    deck = fetch_deck_artifacts(session_id) if res.run_id else {}
    trace = fetch_tool_calls(res.run_id) if res.run_id else []

    (out / "request.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "user_message": user_message,
                "current_view_page": current_view_page,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (out / "chat_result.json").write_text(
        json.dumps(
            {
                "run_id": res.run_id,
                "intent": res.intent,
                "routing": res.routing,
                "final_first_500": res.final[:500],
                "deck_event": res.deck,
                "error": res.error,
                "events": res.events,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if deck.get("slides_tex"):
        (out / "slides.tex").write_text(deck["slides_tex"], encoding="utf-8")
    if deck.get("pdf_path"):
        pdf_src = Path(deck["pdf_path"])
        if pdf_src.is_absolute() and pdf_src.exists():
            pdf_dst = out / "slides.pdf"
            pdf_dst.write_bytes(pdf_src.read_bytes())
        else:
            print(f"[run] WARN deck.pdf_path not readable: {pdf_src}")
    if deck.get("speaker_notes_json"):
        (out / "speaker_notes.json").write_text(
            json.dumps(deck["speaker_notes_json"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if trace:
        (out / "tool_calls.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(
        f"[run] run_id={res.run_id} intent={res.intent} "
        f"deck_id={deck.get('deck_id')} pages={deck.get('page_count')} "
        f"has_notes={deck.get('has_notes')}"
    )
    return {"chat": res, "deck": deck, "trace": trace}
