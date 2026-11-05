"""arXiv's API sometimes returns a single author as several whitespace-led
fragments (`' Mojtaba', ' Komeili'`) or a group prefix with stray punctuation
(`' NVIDIA', ' :'`). clean_author_names repairs those; repair_author_lists
fixes rows already stored with them."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest

from paperhub.pipelines.author_names import clean_author_names, repair_author_lists


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Clean list: untouched.
        (["Ada Lovelace", "Alan Turing"], ["Ada Lovelace", "Alan Turing"]),
        # One author split into whitespace-led fragments -> merged.
        (
            ["Russell Howes", " Mojtaba", " Komeili", "Matthew Muckley"],
            ["Russell Howes", "Mojtaba Komeili", "Matthew Muckley"],
        ),
        # Group prefix + stray punctuation fragment.
        ([" NVIDIA", " :", "Johan Bjorck"], ["NVIDIA", "Johan Bjorck"]),
        # Punctuation-only / empty entries dropped; inner whitespace collapsed.
        (["Ada  Lovelace ", ",", "", "  "], ["Ada Lovelace"]),
        # Non-ASCII names count as real names.
        (["Fernando Castañeda", "王小明"], ["Fernando Castañeda", "王小明"]),
    ],
)
def test_clean_author_names(raw: list[str], expected: list[str]) -> None:
    assert clean_author_names(raw) == expected


def test_arxiv_results_use_clean_author_names() -> None:
    from paperhub.pipelines.arxiv_client import fetch_arxiv_by_ids

    r = MagicMock()
    r.entry_id = "http://arxiv.org/abs/2506.09985v1"
    r.title = "V-JEPA 2"
    names = ["Mido Assran", " Mojtaba", " Komeili"]
    r.authors = [MagicMock() for _ in names]
    for a, n in zip(r.authors, names, strict=True):
        a.name = n
    r.published.year = 2025
    r.summary = "abs"
    r.pdf_url = None
    with patch("paperhub.pipelines.arxiv_client._metadata_client") as client:
        client.results.return_value = iter([r])
        out = fetch_arxiv_by_ids(["2506.09985"])
    assert out is not None
    assert out["2506.09985"].authors == ["Mido Assran", "Mojtaba Komeili"]


async def _insert(conn: aiosqlite.Connection, key: str, authors: list[str]) -> int:
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, authors_json, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, 'arxiv', ?, 't', ?, 's', 'd', 'h')",
        (f"arxiv:{key}", key, json.dumps(authors)),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _authors(conn: aiosqlite.Connection, pcid: int) -> str:
    async with conn.execute(
        "SELECT authors_json FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return str(row[0])


@pytest.mark.asyncio
async def test_repair_author_lists_fixes_only_malformed_rows(
    migrated_db: aiosqlite.Connection,
) -> None:
    bad = await _insert(migrated_db, "2506.09985", ["A B", " Mojtaba", " Komeili"])
    good_json = json.dumps(["Ada Lovelace"])
    good = await _insert(migrated_db, "2401.00001", ["Ada Lovelace"])

    assert await repair_author_lists(migrated_db) == 1
    assert json.loads(await _authors(migrated_db, bad)) == ["A B", "Mojtaba Komeili"]
    # A clean row is not rewritten (byte-identical JSON).
    assert await _authors(migrated_db, good) == good_json
    # Idempotent.
    assert await repair_author_lists(migrated_db) == 0
