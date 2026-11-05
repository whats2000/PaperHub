"""arXiv metadata backfill: when ingest ran while the arXiv API was rate-limited,
the paper was stored with a source-derived title and empty authors/year. The
background backfill fills those in later with ONE batched id_list request per
pass, backing off exponentially while arXiv stays unavailable."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest

from paperhub.pipelines.arxiv_client import ArxivResult, fetch_arxiv_by_ids
from paperhub.pipelines.arxiv_metadata_backfill import (
    backfill_once,
    next_delay,
    run_backfill,
)


def _fake_entry(entry_id: str, title: str) -> MagicMock:
    r = MagicMock()
    r.entry_id = entry_id
    r.title = f" {title} "
    a = MagicMock()
    a.name = "Ada Lovelace"
    r.authors = [a]
    r.published.year = 2025
    r.summary = " An abstract. "
    r.pdf_url = None
    return r


# ---------------------------------------------------------------------------
# fetch_arxiv_by_ids
# ---------------------------------------------------------------------------


def test_fetch_arxiv_by_ids_keys_results_by_bare_id() -> None:
    with patch("paperhub.pipelines.arxiv_client._metadata_client") as client:
        client.results.return_value = iter([
            _fake_entry("http://arxiv.org/abs/2510.21771v2", "Robots"),
            _fake_entry("http://arxiv.org/abs/2503.14734v1", "GR00T"),
        ])
        out = fetch_arxiv_by_ids(["2510.21771", "2503.14734"])

    assert out is not None
    assert set(out) == {"2510.21771", "2503.14734"}
    r = out["2510.21771"]
    assert isinstance(r, ArxivResult)
    assert r.title == "Robots"
    assert r.authors == ["Ada Lovelace"]
    assert r.year == 2025
    # ONE request for the whole batch.
    assert client.results.call_count == 1


def test_fetch_arxiv_by_ids_returns_none_when_api_unavailable() -> None:
    with patch("paperhub.pipelines.arxiv_client._metadata_client") as client:
        client.results.side_effect = RuntimeError("HTTP 429")
        assert fetch_arxiv_by_ids(["2510.21771"]) is None


def test_fetch_arxiv_by_ids_empty_input_makes_no_request() -> None:
    with patch("paperhub.pipelines.arxiv_client._metadata_client") as client:
        assert fetch_arxiv_by_ids([]) == {}
        client.results.assert_not_called()


def test_metadata_client_does_not_retry() -> None:
    """Rate limits last long; per-call retries only prolong them."""
    from paperhub.pipelines import arxiv_client

    assert arxiv_client._metadata_client.num_retries == 0


# ---------------------------------------------------------------------------
# backfill_once
# ---------------------------------------------------------------------------


async def _insert(
    conn: aiosqlite.Connection,
    *,
    arxiv_id: str | None,
    title: str,
    authors: list[str],
    year: int | None,
    abstract: str = "",
    kind: str = "arxiv",
) -> int:
    key = f"arxiv:{arxiv_id}" if arxiv_id else f"sha256:{title}"
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, sha256, title, "
        "authors_json, year, abstract, source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 's', 'd', 'h')",
        (key, kind, arxiv_id, None if arxiv_id else title, title,
         json.dumps(authors), year, abstract),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _row(conn: aiosqlite.Connection, pcid: int) -> tuple[object, ...]:
    async with conn.execute(
        "SELECT title, authors_json, year, abstract FROM paper_content WHERE id = ?",
        (pcid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return tuple(row)


@pytest.mark.asyncio
async def test_backfill_once_fills_missing_metadata(
    migrated_db: aiosqlite.Connection,
) -> None:
    pending = await _insert(
        migrated_db, arxiv_id="2510.21771", title="Source Title", authors=[], year=None,
    )
    complete = await _insert(
        migrated_db, arxiv_id="2401.00001", title="Done", authors=["X"], year=2024,
    )
    await _insert(
        migrated_db, arxiv_id=None, title="upload", authors=[], year=None,
        kind="pdf_upload",
    )
    result = ArxivResult(
        arxiv_id="2510.21771", title="Canonical Title", authors=["Ada"],
        year=2025, abstract="Abs.",
    )
    with patch(
        "paperhub.pipelines.arxiv_metadata_backfill.fetch_arxiv_by_ids",
        return_value={"2510.21771": result},
    ) as fetch:
        ok = await backfill_once(migrated_db)

    assert ok is True
    # Only the pending arxiv row was requested — in a single batch.
    fetch.assert_called_once_with(["2510.21771"])
    assert await _row(migrated_db, pending) == (
        "Canonical Title", '["Ada"]', 2025, "Abs.",
    )
    assert await _row(migrated_db, complete) == ("Done", '["X"]', 2024, "")


@pytest.mark.asyncio
async def test_backfill_once_keeps_existing_abstract(
    migrated_db: aiosqlite.Connection,
) -> None:
    pcid = await _insert(
        migrated_db, arxiv_id="2503.14734", title="GR00T", authors=[], year=2025,
        abstract="Semantic Scholar abstract.",
    )
    result = ArxivResult(
        arxiv_id="2503.14734", title="GR00T N1", authors=["NVIDIA"],
        year=2025, abstract="arXiv abstract.",
    )
    with patch(
        "paperhub.pipelines.arxiv_metadata_backfill.fetch_arxiv_by_ids",
        return_value={"2503.14734": result},
    ):
        assert await backfill_once(migrated_db) is True

    assert await _row(migrated_db, pcid) == (
        "GR00T N1", '["NVIDIA"]', 2025, "Semantic Scholar abstract.",
    )


@pytest.mark.asyncio
async def test_backfill_once_api_unavailable_leaves_rows_untouched(
    migrated_db: aiosqlite.Connection,
) -> None:
    pcid = await _insert(
        migrated_db, arxiv_id="2510.21771", title="Source Title", authors=[], year=None,
    )
    with patch(
        "paperhub.pipelines.arxiv_metadata_backfill.fetch_arxiv_by_ids",
        return_value=None,
    ):
        assert await backfill_once(migrated_db) is False

    assert await _row(migrated_db, pcid) == ("Source Title", "[]", None, "")


@pytest.mark.asyncio
async def test_backfill_once_nothing_pending_makes_no_request(
    migrated_db: aiosqlite.Connection,
) -> None:
    with patch(
        "paperhub.pipelines.arxiv_metadata_backfill.fetch_arxiv_by_ids",
    ) as fetch:
        assert await backfill_once(migrated_db) is True
    fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Backoff + loop
# ---------------------------------------------------------------------------


def test_next_delay_backs_off_exponentially_and_resets() -> None:
    base, cap = 900.0, 21600.0
    assert next_delay(base, ok=False, base_s=base, max_s=cap) == 1800.0
    assert next_delay(1800.0, ok=False, base_s=base, max_s=cap) == 3600.0
    assert next_delay(16000.0, ok=False, base_s=base, max_s=cap) == cap
    assert next_delay(cap, ok=True, base_s=base, max_s=cap) == base


@pytest.mark.asyncio
async def test_run_backfill_exits_promptly_on_stop(
    migrated_db: aiosqlite.Connection,
) -> None:
    stop = asyncio.Event()
    stop.set()
    with patch(
        "paperhub.pipelines.arxiv_metadata_backfill.fetch_arxiv_by_ids",
    ) as fetch:
        await asyncio.wait_for(
            run_backfill(migrated_db, stop=stop, initial_delay_s=0.0), timeout=2,
        )
    fetch.assert_not_called()
