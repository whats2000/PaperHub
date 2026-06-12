"""Tests for sl_seed — deterministic high-level paper map (F6.1)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(tmp_path / "t.db")) as c:
        await apply_schema(c)

        # Paper 73: survey with sections_json populated
        await c.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, arxiv_id, title, abstract, sections_json, "
            " source_path, source_dir_path, html_path) "
            "VALUES (73, 'k73', 'arxiv', '2301.00001', "
            "'A Survey of Vision-Language Models', 'We review...', "
            # real sections_json shape: a list of {name, char_start, ...} dicts
            "'[{\"name\": \"Introduction\", \"char_start\": 0, \"chunk_count\": 1}, "
            "{\"name\": \"Taxonomy\", \"char_start\": 10, \"chunk_count\": 2}, "
            "{\"name\": \"Methods\", \"char_start\": 20, \"chunk_count\": 1}]', "
            "'/p', '/d', '/h')"
        )

        # Paper 74: non-survey, sections_json IS NULL — fallback to chunks
        await c.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, arxiv_id, title, abstract, sections_json, "
            " source_path, source_dir_path, html_path) "
            "VALUES (74, 'k74', 'arxiv', '2301.00002', "
            "'Beyond Language Modeling', 'We present...', NULL, "
            "'/p2', '/d2', '/h2')"
        )
        # Two chunks for paper 74 in different sections (char_start used for stable order)
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (201, 74, 'Method', 0, 10, 'x')"
        )
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (202, 74, 'Results', 20, 30, 'y')"
        )

        await c.commit()
        yield c


@pytest.mark.asyncio
async def test_seed_survey_from_sections_json(conn: aiosqlite.Connection) -> None:
    from paperhub.agents.sl_seed import run_sl_seed

    seeds = await run_sl_seed(paper_ids=[73], conn=conn)
    assert len(seeds) == 1
    s = seeds[0]
    assert s.paper_id == 73
    assert s.is_survey is True
    assert s.sections == ["Introduction", "Taxonomy", "Methods"]
    assert s.abstract.startswith("We review")
    assert s.title == "A Survey of Vision-Language Models"


@pytest.mark.asyncio
async def test_seed_nonsurvey_section_fallback(conn: aiosqlite.Connection) -> None:
    from paperhub.agents.sl_seed import run_sl_seed

    seeds = await run_sl_seed(paper_ids=[74], conn=conn)
    assert len(seeds) == 1
    s = seeds[0]
    assert s.paper_id == 74
    assert s.is_survey is False
    # Fell back to distinct chunks.section — must contain both
    assert set(s.sections) == {"Method", "Results"}


def test_looks_like_survey_pure() -> None:
    from paperhub.agents.sl_seed import _looks_like_survey

    assert _looks_like_survey("A Survey of X", "") is True
    assert _looks_like_survey("A Review of Deep Learning", "") is True
    assert _looks_like_survey("Comprehensive Review of NLP", "") is True
    assert _looks_like_survey("An Overview of Transformers", "") is True
    assert _looks_like_survey("Taxonomy of Visual Models", "") is True
    assert _looks_like_survey("Deep Net", "we present a method") is False
    assert _looks_like_survey("", "a review of recent methods") is True
    assert _looks_like_survey("BERT", "we introduce a model") is False


def test_section_names_from_json_handles_real_dict_shape() -> None:
    from paperhub.agents.sl_seed import section_names_from_json
    # the real column shape: list of {name, char_start, ...} dicts
    raw = '[{"name": "Intro", "char_start": 0}, {"name": "Method", "char_start": 9}]'
    assert section_names_from_json(raw) == ["Intro", "Method"]
    # back-compat: a bare list of strings still works
    assert section_names_from_json('["A", "B"]') == ["A", "B"]
    # malformed / empty → []
    assert section_names_from_json(None) == []
    assert section_names_from_json("") == []
    assert section_names_from_json("not json") == []
    assert section_names_from_json('{"name": "x"}') == []  # not a list


@pytest.mark.asyncio
async def test_seed_figures_empty_when_no_asset(conn: aiosqlite.Connection) -> None:
    """When source_dir has no asset/, figures degrade to empty list (no crash)."""
    from paperhub.agents.sl_seed import run_sl_seed

    seeds = await run_sl_seed(paper_ids=[73], conn=conn)
    s = seeds[0]
    # /d doesn't exist on disk → figures gracefully empty
    assert s.figures == []


@pytest.mark.asyncio
async def test_seed_returns_in_request_order(conn: aiosqlite.Connection) -> None:
    """run_sl_seed returns one SeedPaper per requested paper_id, in order."""
    from paperhub.agents.sl_seed import run_sl_seed

    seeds = await run_sl_seed(paper_ids=[74, 73], conn=conn)
    assert len(seeds) == 2
    assert seeds[0].paper_id == 74
    assert seeds[1].paper_id == 73
