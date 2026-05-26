"""Resolve config source keys against the user's live cache.

A source key in a config is the portable cache identity — ``arxiv:<id>`` or
``sha256:<hash>`` (the ``paper_content.content_key``). We look it up in the
workspace DB so an attach reuses the deduplicated cache (``library:<pc_id>``)
instead of re-ingesting. If an ``arxiv:`` key is not yet cached we fall back to
passing it through for on-demand ingest; an uncached ``sha256:`` upload cannot
be reconstructed, so it errors loudly.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def resolve_attach_id(db_path: str | Path, source_key: str) -> str:
    """Return the ``paper_id`` to POST to /papers for this source key.

    * cached  -> ``library:<pc_id>``  (cheap dedup hit, no re-ingest)
    * arxiv, uncached -> ``arxiv:<id>``  (backend ingests on attach)
    * sha256, uncached -> ValueError (an upload can't be reconstructed)
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id FROM paper_content WHERE content_key = ?", (source_key,)
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        return f"library:{int(row[0])}"
    if source_key.startswith("arxiv:"):
        return source_key  # backend will ingest
    raise ValueError(
        f"source '{source_key}' is not in the cache and cannot be ingested "
        f"(only arxiv: keys ingest on demand). Cache it first, or pick a "
        f"cached source."
    )


def title_for(db_path: str | Path, source_key: str) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT title FROM paper_content WHERE content_key = ?", (source_key,)
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None
