"""The core_agent notes-case seed is safe to run twice on one copy."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio

SEED = (Path(__file__).resolve().parents[2]
        / "benchmark" / "agent" / "corpus" / "core_agent_notes_seed.py")


def _seed_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("notes_seed", SEED)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _workspace(tmp_path: Path) -> Path:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        for pid, sections in ((47, ["Introduction", "Method", "Experiments",
                                    "Limitations"]),
                              (48, ["Background", "Auxiliary-Loss-Free Load "
                                    "Balancing Strategy", "Discussion"]),
                              (68, ["BERT"])):
            await conn.execute(
                "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
                "source_path, source_dir_path, html_path, sections_json) "
                "VALUES (?, ?, 'arxiv', ?, ?, '/x', '/x', '/x', ?)",
                (pid, f"arxiv:{pid}", str(pid), f"Paper {pid}",
                 json.dumps([{"name": s} for s in sections])))
        await conn.commit()
    return tmp_path


def _counts(ws: Path) -> tuple[int, int, int]:
    conn = sqlite3.connect(ws / "paperhub.db")
    try:
        blocks = conn.execute(
            "SELECT COUNT(*) FROM note_blocks WHERE deleted_at IS NULL").fetchone()[0]
        notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        sessions = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    finally:
        conn.close()
    return int(blocks), int(notes), int(sessions)


async def test_second_seed_adds_nothing(tmp_path: Path) -> None:
    mod = _seed_module()
    ws = await _workspace(tmp_path)
    first_ids = await mod.seed(ws)
    first = _counts(ws)
    assert first[0] == 10 + 54 + 1  # paper 48, paper 47 (sections incl.), chat
    second_ids = await mod.seed(ws)
    assert _counts(ws) == first
    assert second_ids == first_ids
