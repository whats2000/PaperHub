import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
import pytest_asyncio

# Tests must run with in-process models — the HTTP-client embedder /
# reranker would otherwise try to dial the (non-running) model server
# and fail. Set before any paperhub.config import resolves the env so
# load_settings() sees it. Must happen at module import time, not
# inside a fixture.
os.environ.setdefault("PAPERHUB_INPROCESS_MODELS", "1")

from paperhub.db.migrate import apply_schema  # noqa: E402
from paperhub.pipelines.paper_pipeline import PaperPipeline  # noqa: E402
from paperhub.tracing.tracer import Tracer  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_model_singletons() -> Iterator[None]:
    """Clear cached embedder/reranker singletons between tests so a
    test that monkeypatches PAPERHUB_INPROCESS_MODELS doesn't leak
    its choice to neighbours. Yields nothing — just brackets the test."""
    from paperhub.pipelines import embedder as _embedder_mod
    from paperhub.rag import reranker as _reranker_mod

    _embedder_mod.reset_singleton()
    _reranker_mod.reset_singleton()
    yield
    _embedder_mod.reset_singleton()
    _reranker_mod.reset_singleton()


@pytest_asyncio.fixture
async def migrated_db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        yield conn


@pytest_asyncio.fixture
async def fake_tracer(migrated_db: aiosqlite.Connection) -> Tracer:
    """Real Tracer bound to a real runs row.

    `step()` writes to `tool_calls` for free — tests don't assert on those
    rows, but the real shape ensures we exercise the contract.
    """
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    async with migrated_db.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    run_id = int(row[0])
    return Tracer(migrated_db, run_id=run_id, branch="")


@pytest_asyncio.fixture
def fake_pipeline() -> MagicMock:
    """MagicMock(spec=PaperPipeline). paper_search tests patch
    ``add_paper_to_session_dispatch`` directly, so the pipeline is never
    actually called in the loop tests."""
    return MagicMock(spec=PaperPipeline)


@pytest_asyncio.fixture
async def seed_library(migrated_db: aiosqlite.Connection) -> int:
    """Insert one paper_content row that the agent can hit via search_library.

    Returns the inserted paper_content_id. The row is NOT attached to any
    session — that's the point: search_library finds it across-session.
    """
    await migrated_db.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "arxiv:1706.03762",
            "arxiv",
            "1706.03762",
            "Attention Is All You Need",
            "[]",
            2017,
            "We propose a new simple network architecture, the Transformer.",
            "/tmp/source.tex",
            "/tmp",
            "/tmp/source.html",
        ),
    )
    await migrated_db.commit()
    async with migrated_db.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])
