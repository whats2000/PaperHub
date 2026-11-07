"""Citation-edge background job: S2 edges between library documents."""
from __future__ import annotations

import asyncio
from typing import Any

import aiosqlite
import httpx

from paperhub.ideation.citations import fetch_once, run_citation_loop
from paperhub.pipelines.semantic_scholar import (
    RelatedPaper,
    SemanticScholarRateLimitError,
)


async def _doc(
    conn: aiosqlite.Connection, arxiv_id: str, *, private: bool = False,
) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path, is_private) "
        "VALUES (?, 'arxiv', ?, ?, '', '', '', ?)",
        (f"arxiv:{arxiv_id}", arxiv_id, f"T{arxiv_id}", int(private)),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _rp(aid: str | None) -> RelatedPaper:
    return RelatedPaper(title="x", abstract="", year=None, authors=[], arxiv_id=aid)


def _related(table: dict[tuple[str, str], list[str | None]], calls: list[Any]) -> Any:
    async def fn(
        arxiv_id: str, *, mode: str, max_results: int = 8,
    ) -> list[RelatedPaper]:
        calls.append((arxiv_id, mode))
        return [_rp(a) for a in table.get((arxiv_id, mode), [])]
    return fn


async def _rows(conn: aiosqlite.Connection, sql: str) -> list[tuple[Any, ...]]:
    async with conn.execute(sql) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def test_splits_internal_and_external(migrated_db: aiosqlite.Connection) -> None:
    a = await _doc(migrated_db, "1111.00001")
    b = await _doc(migrated_db, "1111.00002")
    calls: list[Any] = []
    table: dict[tuple[str, str], list[str | None]] = {
        ("1111.00001", "cites"): ["1111.00002", "9999.00001", None],
        ("1111.00001", "cited_by"): ["8888.00001"],
        ("1111.00002", "cites"): [],
        ("1111.00002", "cited_by"): ["1111.00001"],
    }
    assert await fetch_once(migrated_db, related=_related(table, calls), pause_s=0)
    edges = await _rows(migrated_db, "SELECT source_id, target_id FROM citation_edges")
    # a cites b is the same edge as b cited_by a.
    assert edges == [(a, b)]
    ext = await _rows(
        migrated_db,
        "SELECT paper_content_id, external_arxiv_id, direction "
        "FROM citation_external ORDER BY external_arxiv_id",
    )
    assert ext == [(a, "8888.00001", "cited_by"), (a, "9999.00001", "cites")]
    status = await _rows(
        migrated_db, "SELECT paper_content_id, status FROM citation_fetch ORDER BY 1",
    )
    assert status == [(a, "ok"), (b, "ok")]


async def test_private_document_never_queried(migrated_db: aiosqlite.Connection) -> None:
    await _doc(migrated_db, "1111.00003", private=True)
    calls: list[Any] = []
    assert await fetch_once(migrated_db, related=_related({}, calls), pause_s=0)
    assert calls == []
    assert await _rows(migrated_db, "SELECT * FROM citation_fetch") == []


async def test_rate_limit_returns_false_without_fetch_row(
    migrated_db: aiosqlite.Connection,
) -> None:
    await _doc(migrated_db, "1111.00004")

    async def boom(
        arxiv_id: str, *, mode: str, max_results: int = 8,
    ) -> list[RelatedPaper]:
        raise SemanticScholarRateLimitError("429")

    assert await fetch_once(migrated_db, related=boom, pause_s=0) is False
    assert await _rows(migrated_db, "SELECT * FROM citation_fetch") == []


async def test_other_error_recorded_and_pass_continues(
    migrated_db: aiosqlite.Connection,
) -> None:
    a = await _doc(migrated_db, "1111.00005")
    b = await _doc(migrated_db, "1111.00006")

    async def fn(
        arxiv_id: str, *, mode: str, max_results: int = 8,
    ) -> list[RelatedPaper]:
        if arxiv_id == "1111.00005":
            raise ValueError("bad")
        return []

    assert await fetch_once(migrated_db, related=fn, pause_s=0) is True
    status = await _rows(
        migrated_db, "SELECT paper_content_id, status FROM citation_fetch ORDER BY 1",
    )
    assert status == [(a, "error:ValueError"), (b, "ok")]


async def test_second_pass_skips_fetched(migrated_db: aiosqlite.Connection) -> None:
    await _doc(migrated_db, "1111.00007")
    calls: list[Any] = []
    rel = _related({}, calls)
    await fetch_once(migrated_db, related=rel, pause_s=0)
    assert len(calls) == 2
    await fetch_once(migrated_db, related=rel, pause_s=0)
    assert len(calls) == 2


async def test_stale_row_is_refetched(migrated_db: aiosqlite.Connection) -> None:
    d = await _doc(migrated_db, "1111.00008")
    await migrated_db.execute(
        "INSERT INTO citation_fetch (paper_content_id, fetched_at, status) "
        "VALUES (?, datetime('now','-31 days'), 'ok')", (d,),
    )
    await migrated_db.commit()
    calls: list[Any] = []
    await fetch_once(migrated_db, related=_related({}, calls), pause_s=0)
    assert len(calls) == 2


async def test_loop_stops_on_event(migrated_db: aiosqlite.Connection) -> None:
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(
        run_citation_loop(migrated_db, stop=stop, initial_delay_s=0), timeout=2,
    )


async def test_batch_limited_to_ten(migrated_db: aiosqlite.Connection) -> None:
    for i in range(12):
        await _doc(migrated_db, f"2222.{i:05d}")
    calls: list[Any] = []
    await fetch_once(migrated_db, related=_related({}, calls), pause_s=0)
    assert len({c[0] for c in calls}) == 10


async def test_version_and_case_normalised(migrated_db: aiosqlite.Connection) -> None:
    a = await _doc(migrated_db, "1111.00002")
    b = await _doc(migrated_db, "1111.00009")
    table: dict[tuple[str, str], list[str | None]] = {
        ("1111.00009", "cites"): ["1111.00002V3"],
    }
    await fetch_once(migrated_db, related=_related(table, []), pause_s=0)
    edges = await _rows(migrated_db, "SELECT source_id, target_id FROM citation_edges")
    assert edges == [(b, a)]
    assert await _rows(migrated_db, "SELECT * FROM citation_external") == []


async def test_error_retry_after_one_day_not_before(
    migrated_db: aiosqlite.Connection,
) -> None:
    fresh = await _doc(migrated_db, "1111.00011")
    old = await _doc(migrated_db, "1111.00012")
    for d, age in ((fresh, "-1 hours"), (old, "-2 days")):
        await migrated_db.execute(
            "INSERT INTO citation_fetch (paper_content_id, fetched_at, status) "
            "VALUES (?, datetime('now', ?), 'error:ValueError')", (d, age),
        )
    await migrated_db.commit()
    calls: list[Any] = []
    await fetch_once(migrated_db, related=_related({}, calls), pause_s=0)
    assert {c[0] for c in calls} == {"1111.00012"}


async def test_rate_limit_on_second_doc_keeps_first(
    migrated_db: aiosqlite.Connection,
) -> None:
    a = await _doc(migrated_db, "1111.00013")
    await _doc(migrated_db, "1111.00014")

    async def fn(
        arxiv_id: str, *, mode: str, max_results: int = 8,
    ) -> list[RelatedPaper]:
        if arxiv_id == "1111.00014":
            raise SemanticScholarRateLimitError("429")
        return [_rp("7777.00001")] if mode == "cites" else []

    assert await fetch_once(migrated_db, related=fn, pause_s=0) is False
    assert await _rows(
        migrated_db, "SELECT paper_content_id, status FROM citation_fetch",
    ) == [(a, "ok")]
    assert len(await _rows(migrated_db, "SELECT * FROM citation_external")) == 1


async def test_private_doc_is_never_an_edge_endpoint(
    migrated_db: aiosqlite.Connection,
) -> None:
    await _doc(migrated_db, "1111.00015")
    await _doc(migrated_db, "1111.00016", private=True)
    table: dict[tuple[str, str], list[str | None]] = {
        ("1111.00015", "cites"): ["1111.00016"],
    }
    await fetch_once(migrated_db, related=_related(table, []), pause_s=0)
    assert await _rows(migrated_db, "SELECT * FROM citation_edges") == []


async def test_404_recorded_as_not_found_with_long_retry(
    migrated_db: aiosqlite.Connection,
) -> None:
    d = await _doc(migrated_db, "1111.00017")

    async def fn(
        arxiv_id: str, *, mode: str, max_results: int = 8,
    ) -> list[RelatedPaper]:
        req = httpx.Request("GET", "https://x")
        raise httpx.HTTPStatusError(
            "nf", request=req, response=httpx.Response(404, request=req),
        )

    assert await fetch_once(migrated_db, related=fn, pause_s=0) is True
    assert await _rows(
        migrated_db, "SELECT paper_content_id, status FROM citation_fetch",
    ) == [(d, "not_found")]
    await migrated_db.execute(
        "UPDATE citation_fetch SET fetched_at = datetime('now','-2 days')",
    )
    await migrated_db.commit()
    calls: list[Any] = []
    await fetch_once(migrated_db, related=_related({}, calls), pause_s=0)
    assert calls == []


async def test_client_error_log_includes_status_and_body_head(
    migrated_db: aiosqlite.Connection, caplog: Any,
) -> None:
    """A non-404 HTTP failure logs the status + body head (enrich-then-diagnose)."""
    await _doc(migrated_db, "1111.00018")
    body = '{"error":"Unrecognized or unsupported fields: [authors.name]"}'

    async def fn(
        arxiv_id: str, *, mode: str, max_results: int = 8,
    ) -> list[RelatedPaper]:
        req = httpx.Request("GET", "https://x?api_key=SECRET")
        raise httpx.HTTPStatusError(
            "bad", request=req, response=httpx.Response(400, request=req, text=body),
        )

    caplog.set_level("WARNING", logger="paperhub.ideation.citations")
    assert await fetch_once(migrated_db, related=fn, pause_s=0) is True
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "status=400" in msg
    assert "Unrecognized or unsupported fields" in msg
    assert await _rows(
        migrated_db, "SELECT status FROM citation_fetch",
    ) == [("error:HTTP400",)]
