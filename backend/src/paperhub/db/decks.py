"""decks-table accessors (F4.5 schema, SRS v2.25).

The ``theme`` column was dropped in F4.5 — preamble is the source of truth
via ``slide_style_overrides`` → ``slide_style_global`` memory → default file
(see ``agents/style_resolver.py``). ``current_version_id`` was added to
point at the latest ``edit_history/version_*.json`` snapshot stamped by
``sl_emit``.

``upsert_deck`` is retained for the F4 NOTES/EDIT sub-flows that still need
to update notes/page_count/status after a recompile; the F4.5 GENERATE path
delegates persistence entirely to ``sl_emit`` which uses direct SQL.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiosqlite


@dataclass
class DeckRow:
    id: int
    session_id: int
    run_id: int | None
    tex_path: str
    pdf_path: str | None
    speaker_notes: dict[str, str]
    plan: dict[str, Any]
    page_count: int
    current_version_id: str | None
    contributing_paper_ids: list[int]
    status: str
    created_at: str
    updated_at: str


async def upsert_deck(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    run_id: int | None,
    tex_path: str,
    pdf_path: str | None,
    speaker_notes: dict[str, str],
    plan: dict[str, Any],
    page_count: int,
    contributing_paper_ids: list[int],
    status: str,
    current_version_id: str | None = None,
) -> None:
    """Insert-or-update the per-session deck row.

    Used by the F4 NOTES/EDIT sub-flows (which preserve the existing
    ``current_version_id``). F4.5 GENERATE goes through ``sl_emit`` instead.
    """
    await conn.execute(
        """
        INSERT INTO decks (session_id, run_id, tex_path, pdf_path, speaker_notes_json,
                           plan_json, page_count, current_version_id,
                           contributing_paper_ids_json, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(session_id) DO UPDATE SET
            run_id=excluded.run_id, tex_path=excluded.tex_path, pdf_path=excluded.pdf_path,
            speaker_notes_json=excluded.speaker_notes_json, plan_json=excluded.plan_json,
            page_count=excluded.page_count,
            current_version_id=COALESCE(excluded.current_version_id, decks.current_version_id),
            contributing_paper_ids_json=excluded.contributing_paper_ids_json,
            status=excluded.status, updated_at=datetime('now')
        """,
        (
            session_id,
            run_id,
            tex_path,
            pdf_path,
            json.dumps(speaker_notes),
            json.dumps(plan),
            page_count,
            current_version_id,
            json.dumps(contributing_paper_ids),
            status,
        ),
    )
    await conn.commit()


async def get_deck(conn: aiosqlite.Connection, *, session_id: int) -> DeckRow | None:
    async with conn.execute(
        "SELECT id, session_id, run_id, tex_path, pdf_path, speaker_notes_json, plan_json, "
        "page_count, current_version_id, contributing_paper_ids_json, status, created_at, updated_at "
        "FROM decks WHERE session_id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return DeckRow(
        id=row[0],
        session_id=row[1],
        run_id=row[2],
        tex_path=row[3],
        pdf_path=row[4],
        speaker_notes=json.loads(row[5] or "{}"),
        plan=json.loads(row[6] or "{}"),
        page_count=row[7],
        current_version_id=row[8],
        contributing_paper_ids=json.loads(row[9] or "[]"),
        status=row[10],
        created_at=row[11],
        updated_at=row[12],
    )
