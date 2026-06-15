import json

import pytest

from paperhub.agents.sl_cite import frame_grounding_json, parse_cite
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema


def test_parse_cite_content_entries() -> None:
    kind, entries = parse_cite("\\begin{frame}\n% cite: 38:1 INTRODUCTION\n...")  # type: ignore[misc]
    assert kind == "content"
    assert entries == [(38, "1 INTRODUCTION")]


def test_parse_cite_multi_paper() -> None:
    kind, entries = parse_cite("% cite: 47:Method; 53:Our Method: ReMoE")  # type: ignore[misc]
    assert kind == "content"
    assert entries == [(47, "Method"), (53, "Our Method: ReMoE")]


def test_parse_cite_structural_and_missing() -> None:
    assert parse_cite("% cite: title") == ("title", [])
    assert parse_cite("% cite: divider") == ("divider", [])
    assert parse_cite("% cite: hallucination") == ("hallucination", [])
    assert parse_cite("\\begin{frame}{No marker}\\end{frame}") is None


async def _seed_chunks(conn) -> None:
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) "
        "VALUES (38, 'ck38', 'arxiv', '2400.00038', 't', '/s', '/d', '/h')"
    )
    await conn.executemany(
        "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (11, 38, "1 INTRODUCTION", 0, 10, "a"),
            (12, 38, "1 INTRODUCTION", 10, 20, "b"),
        ],
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_frame_grounding_resolves_to_chunks(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        await _seed_chunks(conn)
        out = json.loads(await frame_grounding_json("% cite: 38:1 INTRODUCTION\n", conn))
        assert out == [
            {"paper_id": 38, "section_name": "1 INTRODUCTION", "chunk_ids": [11, 12]}
        ]


@pytest.mark.asyncio
async def test_frame_grounding_normalized_match(tmp_path) -> None:
    """A cite differing only in case/spacing still resolves to real chunks."""
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        await _seed_chunks(conn)
        out = json.loads(await frame_grounding_json("% cite: 38:1   introduction\n", conn))
        assert out[0]["chunk_ids"] == [11, 12]


@pytest.mark.asyncio
async def test_frame_grounding_unsourced_is_recorded_empty(tmp_path) -> None:
    """A cite to a nonexistent section is recorded with empty chunk_ids, not dropped."""
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        await _seed_chunks(conn)
        out = json.loads(await frame_grounding_json("% cite: 38:Ghost Section\n", conn))
        assert out == [
            {"paper_id": 38, "section_name": "Ghost Section", "chunk_ids": []}
        ]


@pytest.mark.asyncio
async def test_frame_grounding_structural_is_empty(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        await _seed_chunks(conn)
        assert await frame_grounding_json("% cite: title\n", conn) == "[]"
        assert await frame_grounding_json("\\begin{frame}{x}\\end{frame}", conn) == "[]"
