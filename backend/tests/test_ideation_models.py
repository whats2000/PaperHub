"""Plan H3 Task 1: ideation contract — models, tables, internal session."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from pydantic import ValidationError

from paperhub.db.migrate import apply_schema, sweep_orphan_session_folders
from paperhub.ideation.internal_session import ideation_session_id
from paperhub.ideation.models import DISMISS_REASONS, Idea, MechanismCard

TABLES = (
    "mechanism_cards", "domains", "document_domains", "citation_edges",
    "citation_external", "citation_fetch", "problems", "ideas", "idea_sessions",
)


def test_idea_rejects_unknown_status() -> None:
    base = {"id": 1, "source_doc": 2, "source_section": "s", "title": "t",
            "created_at": "x"}
    assert Idea(**base).status == "new"
    with pytest.raises(ValidationError):
        Idea(**base, status="bogus")


def test_card_and_reasons() -> None:
    card = MechanismCard.model_validate({
        "problem": {"text": "p", "sections": ["Intro"]},
        "mechanism": {"text": "m", "sections": []},
    })
    assert card.assumptions == [] and card.domains == []
    assert "analogy_fails" in DISMISS_REASONS and len(DISMISS_REASONS) == 5


@pytest.mark.asyncio
async def test_ideation_tables_and_internal_column_idempotent(
    tmp_path: Path,
) -> None:
    async with aiosqlite.connect(tmp_path / "i.db") as conn:
        await apply_schema(conn)
        await apply_schema(conn)
        for t in TABLES:
            async with conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ) as cur:
                assert await cur.fetchone() is not None, t
        async with conn.execute("PRAGMA table_info(chat_sessions)") as cur:
            assert "internal" in {r[1] for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_internal_column_added_to_existing_db(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "old.db") as conn:
        await conn.execute(
            "CREATE TABLE chat_sessions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            " title TEXT NOT NULL DEFAULT 'New chat')"
        )
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.commit()
        await apply_schema(conn)
        await apply_schema(conn)
        async with conn.execute("SELECT internal FROM chat_sessions") as cur:
            assert [r[0] for r in await cur.fetchall()] == [0]


@pytest.mark.asyncio
async def test_internal_session_created_once_and_not_swept(
    tmp_path: Path,
) -> None:
    async with aiosqlite.connect(tmp_path / "i.db") as conn:
        await apply_schema(conn)
        a = await ideation_session_id(conn)
        b = await ideation_session_id(conn)
        assert a == b
        async with conn.execute(
            "SELECT COUNT(*), MAX(internal), MAX(title) FROM chat_sessions"
        ) as cur:
            assert await cur.fetchone() == (1, 1, "Ideation (internal)")
        folder = tmp_path / "chat_session" / str(a)
        folder.mkdir(parents=True)
        assert await sweep_orphan_session_folders(conn, tmp_path) == 0
        assert folder.exists()


@pytest.mark.asyncio
async def test_internal_session_concurrent_creation_single_row(
    tmp_path: Path,
) -> None:
    import asyncio

    db = tmp_path / "c.db"
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)

    async def one() -> int:
        async with aiosqlite.connect(db, timeout=30) as c:
            return await ideation_session_id(c)

    ids = await asyncio.gather(one(), one(), one())
    assert len(set(ids)) == 1
    async with aiosqlite.connect(db) as conn, conn.execute(
        "SELECT COUNT(*) FROM chat_sessions WHERE internal = 1"
    ) as cur:
        assert (await cur.fetchone()) == (1,)


@pytest.mark.asyncio
async def test_internal_session_tombstone_cleared(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "t.db") as conn:
        await apply_schema(conn)
        sid = await ideation_session_id(conn)
        await conn.execute(
            "UPDATE chat_sessions SET deleted_at = datetime('now') WHERE id = ?",
            (sid,),
        )
        await conn.commit()
        assert await ideation_session_id(conn) == sid
        async with conn.execute(
            "SELECT deleted_at FROM chat_sessions WHERE id = ?", (sid,)
        ) as cur:
            assert (await cur.fetchone()) == (None,)


def test_feedback_contract_has_one_source() -> None:
    """The API serves the models.py contract (no drifting copy)."""
    from paperhub.api import ideation as api
    from paperhub.ideation import models

    assert api.FeedbackSummary is models.FeedbackSummary
    assert api.FeedbackToG1Response is models.FeedbackToG1Response
    s = models.FeedbackSummary(
        save_rate=0.5, worked_rate=None,
        dismiss_reasons=[{"reason": "not_novel", "count": 2}],  # type: ignore[list-item]
    )
    assert s.worked_rate is None
    assert s.dismiss_reasons[0] == models.DismissReasonCount(reason="not_novel", count=2)
