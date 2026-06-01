"""F4.5 sl_emit - deterministic finalize stage (3rd and last).

Runs AFTER the slide_agent returns satisfied=True (or budget-exhausted with
deck content). Responsibilities:
  1. Contract #1 enforcement - ``verify_and_fix_graphics`` audits every
     ``\\includegraphics`` key against the inventory; unknown keys become
     ``\\textit{[figure omitted]}``. NEVER prompts the LLM (deterministic).
  2. Persist decks + deck_slides rows (one current deck per session per the
     ``UNIQUE(session_id)`` constraint; ``deck_slides`` rebuilt from the
     post-audit frames).
  3. Snapshot the new (tex, speaker_notes) under
     ``edit_history/version_<ts>.json``.
  4. Update ``decks.current_version_id`` to point at the new snapshot.
  5. The caller (report_graph) emits the ``deck`` SSE event from the
     ``EmitResult``.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from paperhub.models.slide_domain import KeyFigureBundle
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
)
from paperhub.pipelines.slide_pipeline.figure_inventory import (
    verify_and_fix_graphics,
)


@dataclass(frozen=True)
class EmitResult:
    deck_id: int
    deck_tex: str  # post-audit (may differ from input on unknown-key replacements)
    page_count: int
    current_version_id: str
    figure_audit_replacements: int  # how many \includegraphics were replaced


def _frame_spans(deck_tex: str) -> list[tuple[str, int, int]]:
    """Return ``[(frame_tex, page_start, page_end), ...]`` in source order.

    ``extract_frames_from_beamer`` already duplicates each frame across its
    overlay pages (page numbers align with the rendered PDF), so collapsing
    by frame body gives ``(content, first_page, last_page)`` per logical
    frame.
    """
    raw = extract_frames_from_beamer(deck_tex)
    if not raw:
        return []
    spans: list[tuple[str, int, int]] = []
    cur_content = raw[0][1]
    cur_start = raw[0][0]
    cur_end = raw[0][0]
    for page_num, content, _s, _e in raw[1:]:
        if content == cur_content and page_num == cur_end + 1:
            cur_end = page_num
            continue
        spans.append((cur_content, cur_start, cur_end))
        cur_content = content
        cur_start = page_num
        cur_end = page_num
    spans.append((cur_content, cur_start, cur_end))
    return spans


async def run_sl_emit(
    *,
    session_id: int,
    run_id: int,
    deck_tex: str,
    workdir: Path,
    page_count: int,
    status: str,  # 'ok' | 'error'
    contributing_paper_ids: list[int],
    figure_inventory: dict[str, KeyFigureBundle],
    conn: aiosqlite.Connection,
    speaker_notes: dict[int, str] | None = None,  # opt-in NOTES path
) -> EmitResult:
    # 1. Contract #1: figure-key audit.
    inventory_keys: set[str] = set(figure_inventory.keys())
    audited_tex, rejected = verify_and_fix_graphics(
        deck_tex, allowed_keys=inventory_keys
    )
    n_replacements = len(rejected)

    # 2. + 3. Filesystem work off the event loop (write audited deck.tex,
    # write the version snapshot under edit_history/).
    deck_path = workdir / "deck.tex"
    pdf_path = workdir / "deck.pdf"
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    version_id = f"version_{ts}"
    snapshot = {
        "tex_content": audited_tex,
        "speaker_notes": {str(k): v for k, v in (speaker_notes or {}).items()},
        "description": "F4.5 sl_emit snapshot",
        "timestamp": ts,
    }

    def _persist_files() -> bool:
        workdir.mkdir(parents=True, exist_ok=True)
        deck_path.write_text(audited_tex, encoding="utf-8")
        edit_history = workdir / "edit_history"
        edit_history.mkdir(exist_ok=True)
        (edit_history / f"{version_id}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return pdf_path.exists()

    pdf_exists = await asyncio.to_thread(_persist_files)

    # 4. Upsert the decks row.
    speaker_notes_json = (
        json.dumps(
            {str(k): v for k, v in (speaker_notes or {}).items()},
            ensure_ascii=False,
        )
        if speaker_notes
        else None
    )

    await conn.execute(
        """
        INSERT INTO decks (
            session_id, run_id, tex_path, pdf_path, speaker_notes_json,
            plan_json, page_count, current_version_id,
            contributing_paper_ids_json, status, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, datetime('now'), datetime('now')
        )
        ON CONFLICT(session_id) DO UPDATE SET
            run_id = excluded.run_id,
            tex_path = excluded.tex_path,
            pdf_path = excluded.pdf_path,
            speaker_notes_json = excluded.speaker_notes_json,
            page_count = excluded.page_count,
            current_version_id = excluded.current_version_id,
            contributing_paper_ids_json = excluded.contributing_paper_ids_json,
            status = excluded.status,
            updated_at = datetime('now')
        """,
        (
            session_id,
            run_id,
            str(deck_path),
            str(pdf_path) if pdf_exists else None,
            speaker_notes_json,
            page_count,
            version_id,
            json.dumps(contributing_paper_ids),
            status,
        ),
    )

    async with conn.execute(
        "SELECT id FROM decks WHERE session_id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"sl_emit: decks row not found for session_id={session_id} after upsert"
        )
    deck_id = int(row[0])

    # 5. Rebuild deck_slides rows. Earlier rows (if any) are cleared because
    # frame_count likely changed; notes are reapplied by index from `speaker_notes`.
    await conn.execute("DELETE FROM deck_slides WHERE deck_id = ?", (deck_id,))
    spans = _frame_spans(audited_tex)
    for idx, (frame_tex, page_start, page_end) in enumerate(spans):
        note_text = (speaker_notes or {}).get(idx)
        await conn.execute(
            """
            INSERT INTO deck_slides (
                deck_id, slide_index, frame_tex, note_text, note_language,
                page_start, page_end
            ) VALUES (?, ?, ?, ?, NULL, ?, ?)
            """,
            (deck_id, idx, frame_tex, note_text, page_start, page_end),
        )
    await conn.commit()

    return EmitResult(
        deck_id=deck_id,
        deck_tex=audited_tex,
        page_count=page_count,
        current_version_id=version_id,
        figure_audit_replacements=n_replacements,
    )
