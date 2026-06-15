"""deck_slides CRUD (Plan F4 — SRS v2.21).

One row per final frame of the session's current deck: the frame LaTeX, an
opt-in speaker note in an independent language, and the PDF page span the frame
occupies. `decks.speaker_notes_json` is a DERIVED cache rebuilt from these rows
(kept for the `deck` SSE `has_notes` flag + the GET /deck back-compat shape).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True)
class DeckSlideInput:
    slide_index: int
    frame_tex: str
    page_start: int
    page_end: int
    note_text: str | None = None
    note_language: str | None = None
    # Per-slide source grounding (north-star traceback): a JSON array of
    # {paper_id, section_name, chunk_ids} resolved from the frame's % cite:
    # marker. Defaults to "[]" (ungrounded / structural slide).
    source_sections_json: str = "[]"


@dataclass(frozen=True)
class DeckSlideRow:
    id: int
    deck_id: int
    slide_index: int
    frame_tex: str
    note_text: str | None
    note_language: str | None
    page_start: int
    page_end: int
    source_sections_json: str = "[]"


async def replace_deck_slides(
    conn: aiosqlite.Connection, *, deck_id: int, slides: list[DeckSlideInput]
) -> None:
    """Atomically replace all rows for a deck (used on generate + recreate)."""
    await conn.execute("DELETE FROM deck_slides WHERE deck_id = ?", (deck_id,))
    await conn.executemany(
        "INSERT INTO deck_slides (deck_id, slide_index, frame_tex, note_text, "
        "note_language, page_start, page_end, source_sections_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (deck_id, s.slide_index, s.frame_tex, s.note_text,
             s.note_language, s.page_start, s.page_end, s.source_sections_json)
            for s in slides
        ],
    )
    await conn.commit()


async def get_deck_slides(
    conn: aiosqlite.Connection, *, deck_id: int
) -> list[DeckSlideRow]:
    """Return all frames for `deck_id`, ordered by `slide_index`."""
    async with conn.execute(
        "SELECT id, deck_id, slide_index, frame_tex, note_text, note_language, "
        "page_start, page_end, source_sections_json "
        "FROM deck_slides WHERE deck_id = ? ORDER BY slide_index",
        (deck_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        DeckSlideRow(
            id=r[0], deck_id=r[1], slide_index=r[2], frame_tex=r[3],
            note_text=r[4], note_language=r[5], page_start=r[6], page_end=r[7],
            source_sections_json=r[8] if r[8] is not None else "[]",
        )
        for r in rows
    ]


async def update_slide_note(
    conn: aiosqlite.Connection, *, deck_id: int, slide_index: int,
    note_text: str, note_language: str,
) -> None:
    cur = await conn.execute(
        "UPDATE deck_slides SET note_text = ?, note_language = ? "
        "WHERE deck_id = ? AND slide_index = ?",
        (note_text, note_language, deck_id, slide_index),
    )
    if cur.rowcount == 0:
        raise ValueError(
            f"no deck_slides row for deck_id={deck_id}, slide_index={slide_index}"
        )
    await conn.commit()


async def update_slide_frame(
    conn: aiosqlite.Connection, *, deck_id: int, slide_index: int, frame_tex: str
) -> None:
    cur = await conn.execute(
        "UPDATE deck_slides SET frame_tex = ? WHERE deck_id = ? AND slide_index = ?",
        (frame_tex, deck_id, slide_index),
    )
    if cur.rowcount == 0:
        raise ValueError(
            f"no deck_slides row for deck_id={deck_id}, slide_index={slide_index}"
        )
    await conn.commit()


async def update_slide_grounding(
    conn: aiosqlite.Connection, *, deck_id: int, slide_index: int,
    frame_tex: str, source_sections_json: str,
) -> None:
    """Set one slide's frame_tex + source grounding (the structured Sources
    editor: a comment-only change — no recompile)."""
    cur = await conn.execute(
        "UPDATE deck_slides SET frame_tex = ?, source_sections_json = ? "
        "WHERE deck_id = ? AND slide_index = ?",
        (frame_tex, source_sections_json, deck_id, slide_index),
    )
    if cur.rowcount == 0:
        raise ValueError(
            f"no deck_slides row for deck_id={deck_id}, slide_index={slide_index}"
        )
    await conn.commit()


async def rebuild_speaker_notes_json(
    conn: aiosqlite.Connection, *, deck_id: int
) -> dict[str, str]:
    """Expand per-slide notes into a {page: note} map and write it onto the
    deck row. A slide spanning pages p..q puts its note on page p and
    "(continued)" on p+1..q (the pages a slide spans beyond its first).
    Returns the rebuilt map."""
    rows = await get_deck_slides(conn, deck_id=deck_id)
    notes: dict[str, str] = {}
    for r in rows:
        if r.note_text is None:
            continue
        notes[str(r.page_start)] = r.note_text
        for p in range(r.page_start + 1, r.page_end + 1):
            notes[str(p)] = "(continued)"
    await conn.execute(
        "UPDATE decks SET speaker_notes_json = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (json.dumps(notes, ensure_ascii=False), deck_id),
    )
    await conn.commit()
    return notes


__all__ = [
    "DeckSlideInput", "DeckSlideRow", "replace_deck_slides", "get_deck_slides",
    "update_slide_note", "update_slide_frame", "update_slide_grounding",
    "rebuild_speaker_notes_json",
]
