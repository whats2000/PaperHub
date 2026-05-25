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
    theme: str
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
    theme: str,
    contributing_paper_ids: list[int],
    status: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO decks (session_id, run_id, tex_path, pdf_path, speaker_notes_json,
                           plan_json, page_count, theme, contributing_paper_ids_json, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(session_id) DO UPDATE SET
            run_id=excluded.run_id, tex_path=excluded.tex_path, pdf_path=excluded.pdf_path,
            speaker_notes_json=excluded.speaker_notes_json, plan_json=excluded.plan_json,
            page_count=excluded.page_count, theme=excluded.theme,
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
            theme,
            json.dumps(contributing_paper_ids),
            status,
        ),
    )
    await conn.commit()


async def get_deck(conn: aiosqlite.Connection, *, session_id: int) -> DeckRow | None:
    async with conn.execute(
        "SELECT id, session_id, run_id, tex_path, pdf_path, speaker_notes_json, plan_json, "
        "page_count, theme, contributing_paper_ids_json, status, created_at, updated_at "
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
        theme=row[8],
        contributing_paper_ids=json.loads(row[9] or "[]"),
        status=row[10],
        created_at=row[11],
        updated_at=row[12],
    )
