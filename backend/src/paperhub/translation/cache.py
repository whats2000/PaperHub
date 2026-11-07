"""Translation cache repository (Plan H1): one row per (paper, block key, language)."""

from dataclasses import dataclass

import aiosqlite

from paperhub.translation.placeholders import normalize_language


@dataclass(frozen=True)
class CachedRow:
    source_hash: str
    memory_hash: str
    text: str
    model: str


async def get_cached(
    conn: aiosqlite.Connection,
    paper_content_id: int,
    keys: list[str],
    language: str,
) -> dict[str, CachedRow]:
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    cur = await conn.execute(
        "SELECT block_key, source_hash, memory_hash, text, model FROM translations"
        f" WHERE paper_content_id = ? AND language = ? AND block_key IN ({marks})",
        (paper_content_id, normalize_language(language), *keys),
    )
    return {
        r[0]: CachedRow(source_hash=r[1], memory_hash=r[2], text=r[3], model=r[4])
        for r in await cur.fetchall()
    }


async def put_cached(
    conn: aiosqlite.Connection,
    paper_content_id: int,
    key: str,
    language: str,
    source_hash: str,
    memory_hash: str,
    text: str,
    model: str,
) -> None:
    """Upsert one row. Does not commit: call it inside the caller's
    ``write_transaction`` so the write takes the process-wide write lock."""
    await conn.execute(
        """
        INSERT INTO translations (paper_content_id, block_key, language,
                                  source_hash, memory_hash, text, model)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_content_id, block_key, language) DO UPDATE SET
            source_hash=excluded.source_hash, memory_hash=excluded.memory_hash,
            text=excluded.text, model=excluded.model, created_at=datetime('now')
        """,
        (paper_content_id, key, normalize_language(language),
         source_hash, memory_hash, text, model),
    )
