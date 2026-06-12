"""Tests for paper_digest — cached PaperDigest builder (F6.1-R)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema
from paperhub.models.slide_domain import DigestSection

# ---------------------------------------------------------------------------
# Stub adapter
# ---------------------------------------------------------------------------


class StubAdapter:
    """Minimal stub matching the LlmAdapter.structured Protocol."""

    def __init__(self, *, raise_for: set[str] | None = None) -> None:
        self.call_count = 0
        self._raise_for: set[str] = raise_for or set()

    async def structured(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        response_model: type,
        model: str,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.call_count += 1
        section_name: str = variables.get("section_name", "")
        if section_name in self._raise_for:
            raise RuntimeError(f"stub: forced failure for section '{section_name}'")
        return DigestSection(name=section_name, insight=f"insight for {section_name}")

    def stream(self, **kwargs: Any) -> AsyncIterator[str]:  # type: ignore[override]
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """SQLite connection with schema applied and two test papers seeded."""
    async with aiosqlite.connect(str(tmp_path / "t.db")) as c:
        await apply_schema(c)

        # Paper 10: sections_json populated with 2 sections
        await c.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, arxiv_id, title, abstract, sections_json, "
            " source_path, source_dir_path, html_path) "
            "VALUES (10, 'ck10', 'arxiv', '2400.00010', "
            "'Test Paper Title', 'This is the abstract.', "
            "'[{\"name\": \"Introduction\", \"char_start\": 0, \"chunk_count\": 1},"
            " {\"name\": \"Method\", \"char_start\": 100, \"chunk_count\": 2}]', "
            "'/src10', ?, '/h10')",
            (str(tmp_path),),
        )

        # chunks for paper 10 — Introduction (1 chunk) + Method (2 chunks)
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (1, 10, 'Introduction', 0, 50, 'Intro chunk text.')"
        )
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (2, 10, 'Method', 100, 150, 'Method chunk one.')"
        )
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (3, 10, 'Method', 150, 200, 'Method chunk two.')"
        )

        # Paper 11: sections_json NULL — fallback to chunk sections.
        # source_dir_path is set to a non-existent path so caching is
        # effectively skipped (no dir to write to) while satisfying NOT NULL.
        await c.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, arxiv_id, title, abstract, sections_json, "
            " source_path, source_dir_path, html_path) "
            "VALUES (11, 'ck11', 'arxiv', '2400.00011', "
            "'Another Paper', 'Second abstract.', NULL, "
            "'/src11', '/nonexistent_dir_11', '/h11')"
        )
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (10, 11, 'Background', 0, 30, 'Background text.')"
        )
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (11, 11, 'Conclusion', 100, 130, 'Conclusion text.')"
        )

        await c.commit()
        yield c


# ---------------------------------------------------------------------------
# build_paper_digest — basic smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_paper_digest_sections(
    conn: aiosqlite.Connection,
    tmp_path: Path,
) -> None:
    """build_paper_digest returns one DigestSection per section, insights non-empty."""
    from paperhub.agents.paper_digest import build_paper_digest

    adapter = StubAdapter()
    digest = await build_paper_digest(
        paper_id=10,
        conn=conn,
        asset=None,
        adapter=adapter,
        model="test-model",
    )

    assert digest.paper_id == 10
    assert digest.title == "Test Paper Title"
    assert digest.abstract == "This is the abstract."
    assert len(digest.sections) == 2

    names = [s.name for s in digest.sections]
    assert "Introduction" in names
    assert "Method" in names

    for sec in digest.sections:
        assert sec.insight, f"insight must be non-empty for section {sec.name!r}"

    # Adapter called once per section
    assert adapter.call_count == 2


@pytest.mark.asyncio
async def test_build_paper_digest_known_name_preserved(
    conn: aiosqlite.Connection,
) -> None:
    """Section names come from the DB, not the model — the model only provides insight."""
    from paperhub.agents.paper_digest import build_paper_digest

    class RenameAdapter:
        """Stub that returns a DIFFERENT name in its DigestSection."""

        call_count = 0

        async def structured(
            self,
            *,
            slot: str,
            variables: dict[str, Any],
            response_model: type,
            model: str,
            history: list[dict[str, str]] | None = None,
            **kwargs: Any,
        ) -> DigestSection:
            RenameAdapter.call_count += 1
            # Intentionally return a different name
            return DigestSection(name="RENAMED", insight="some insight")

        def stream(self, **kwargs: Any) -> AsyncIterator[str]:  # type: ignore[override]
            raise NotImplementedError

    adapter = RenameAdapter()
    digest = await build_paper_digest(
        paper_id=10, conn=conn, asset=None, adapter=adapter, model="m"
    )
    names = [s.name for s in digest.sections]
    # Must keep DB names, not "RENAMED"
    assert "Introduction" in names
    assert "Method" in names
    assert "RENAMED" not in names


# ---------------------------------------------------------------------------
# build_paper_digest — per-section degrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_paper_digest_degrade_one_section(
    conn: aiosqlite.Connection,
) -> None:
    """When one section's model call fails, that section gets insight='', others pass."""
    from paperhub.agents.paper_digest import build_paper_digest

    # Fail only the 'Method' section
    adapter = StubAdapter(raise_for={"Method"})
    digest = await build_paper_digest(
        paper_id=10, conn=conn, asset=None, adapter=adapter, model="m"
    )

    assert len(digest.sections) == 2
    by_name = {s.name: s for s in digest.sections}

    # Introduction succeeded
    assert by_name["Introduction"].insight == "insight for Introduction"
    # Method failed — degraded to empty insight
    assert by_name["Method"].insight == ""


# ---------------------------------------------------------------------------
# get_or_build_digest — cache behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_build_digest_cache_miss_then_hit(
    conn: aiosqlite.Connection,
    tmp_path: Path,
) -> None:
    """First call builds + writes digest.json; second call reads from cache (adapter not called again)."""
    from paperhub.agents.paper_digest import get_or_build_digest

    adapter = StubAdapter()

    # First call: cache miss → build + write
    digest1 = await get_or_build_digest(
        paper_id=10, conn=conn, asset=None, adapter=adapter, model="m"
    )
    assert digest1.paper_id == 10
    assert len(digest1.sections) == 2

    cache_path = tmp_path / "digest.json"
    assert cache_path.exists(), "digest.json must be written after first call"

    calls_after_first = adapter.call_count
    assert calls_after_first > 0, "adapter must have been called on cache miss"

    # Second call: cache hit → adapter NOT called again
    digest2 = await get_or_build_digest(
        paper_id=10, conn=conn, asset=None, adapter=adapter, model="m"
    )
    assert adapter.call_count == calls_after_first, (
        "cache hit must not call the adapter again"
    )

    # Returned digests are equivalent
    assert digest2.paper_id == digest1.paper_id
    assert digest2.title == digest1.title
    assert [s.name for s in digest2.sections] == [s.name for s in digest1.sections]


@pytest.mark.asyncio
async def test_get_or_build_digest_nonexistent_source_dir(
    conn: aiosqlite.Connection,
) -> None:
    """When source_dir_path points to a non-existent dir, get_or_build_digest builds without crashing."""
    from paperhub.agents.paper_digest import get_or_build_digest

    adapter = StubAdapter()
    # Paper 11 has a non-existent source_dir_path and chunk-based sections
    digest = await get_or_build_digest(
        paper_id=11, conn=conn, asset=None, adapter=adapter, model="m"
    )
    assert digest.paper_id == 11
    assert len(digest.sections) == 2
    names = [s.name for s in digest.sections]
    assert set(names) == {"Background", "Conclusion"}


@pytest.mark.asyncio
async def test_get_or_build_digest_stale_cache_rebuilds(
    conn: aiosqlite.Connection,
    tmp_path: Path,
) -> None:
    """A corrupt digest.json triggers a rebuild rather than crashing."""
    from paperhub.agents.paper_digest import get_or_build_digest

    # Write intentionally corrupt JSON to the cache path
    cache_path = tmp_path / "digest.json"
    cache_path.write_text("NOT VALID JSON {{{{", encoding="utf-8")

    adapter = StubAdapter()
    digest = await get_or_build_digest(
        paper_id=10, conn=conn, asset=None, adapter=adapter, model="m"
    )
    # Must have rebuilt successfully
    assert digest.paper_id == 10
    assert len(digest.sections) == 2
    assert adapter.call_count > 0, "adapter must be called when cache is invalid"


# ---------------------------------------------------------------------------
# Key equations from PaperAsset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_paper_digest_equations_from_asset(
    conn: aiosqlite.Connection,
) -> None:
    """key_equations is capped at 6 and all have role=''.

    If someone removes the [:_EQUATIONS_CAP] slice this test FAILS.
    """
    from paperhub.agents.paper_digest import build_paper_digest
    from paperhub.pipelines.paper_asset import EquationAsset, PaperAsset

    # Build a PaperAsset with 8 equations — 2 over the cap of 6
    asset = PaperAsset(
        figures=[],
        equations=[
            EquationAsset(id=f"eq{i}", latex=f"x_{i} = y", section="Method")
            for i in range(8)
        ],
    )
    assert len(asset.equations) == 8

    adapter = StubAdapter()
    digest = await build_paper_digest(
        paper_id=10,
        conn=conn,
        asset=asset,
        adapter=adapter,
        model="test-model",
    )

    # Cap must be enforced
    assert len(digest.key_equations) == 6
    # Every carried equation must have role=""
    assert all(eq.role == "" for eq in digest.key_equations)
