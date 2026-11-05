"""Author-name normalization for paper metadata.

arXiv's API sometimes returns one author as several whitespace-led fragments
(V-JEPA 2: ``' Mojtaba', ' Komeili'`` for "Mojtaba Komeili") or a group prefix
with a stray punctuation fragment (GR00T N1: ``' NVIDIA', ' :'``). The leading
whitespace is the fragment signal — a properly parsed name never starts with
it — so a run of consecutive whitespace-led entries is merged into one name.
"""
from __future__ import annotations

import json
import logging
import re

import aiosqlite

logger = logging.getLogger(__name__)

_WORD = re.compile(r"\w")


def clean_author_names(names: list[str]) -> list[str]:
    """Merge whitespace-led fragment runs, drop punctuation-only / empty
    entries, and collapse whitespace. Clean lists come back unchanged."""
    out: list[str] = []
    fragment: list[str] = []

    def flush() -> None:
        if fragment:
            out.append(" ".join(fragment))
            fragment.clear()

    for raw in names:
        if not isinstance(raw, str):
            continue
        name = " ".join(raw.split())
        is_fragment = raw[:1].isspace()
        if not _WORD.search(name):
            if not is_fragment:
                flush()
            continue
        if is_fragment:
            fragment.append(name)
        else:
            flush()
            out.append(name)
    flush()
    return out


async def repair_author_lists(conn: aiosqlite.Connection) -> int:
    """Rewrite stored ``paper_content.authors_json`` lists that
    ``clean_author_names`` would change. Idempotent; clean rows are not
    touched. Returns the number of rows repaired."""
    async with conn.execute("SELECT id, authors_json FROM paper_content") as cur:
        rows = await cur.fetchall()
    repaired = 0
    for pcid, authors_json in rows:
        try:
            names = json.loads(authors_json or "[]")
        except ValueError:
            continue
        if not isinstance(names, list):
            continue
        cleaned = clean_author_names(names)
        if cleaned == names:
            continue
        await conn.execute(
            "UPDATE paper_content SET authors_json = ? WHERE id = ?",
            (json.dumps(cleaned), pcid),
        )
        repaired += 1
    if repaired:
        await conn.commit()
        logger.info("repaired malformed author lists on %d paper(s)", repaired)
    return repaired
