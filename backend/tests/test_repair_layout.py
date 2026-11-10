"""paperhub-repair-layout: stale layout chunk ids are re-pointed by caption."""
from __future__ import annotations

import json

import aiosqlite
import pytest

from paperhub.cli.repair_layout import plan_layout, remap_layout


def test_remap_moves_stale_entries_to_the_caption_chunk() -> None:
    chunks = [(10, "Intro text."), (11, "Fig.  3 | Performance\nevaluation of CNN models. a–f")]
    layout: list[dict[str, object]] = [
        {"label": "Figure 3", "caption": "Fig. 3 | Performance evaluation of CNN models",
         "chunk_id": 77383},
        {"label": None, "caption": None, "chunk_id": 77401},
        {"label": "Figure 1", "caption": "Fig. 1 | x", "chunk_id": 10},
    ]
    new, remapped, dropped = remap_layout(layout, chunks)
    assert [e["chunk_id"] for e in new] == [11, None, 10]
    assert (remapped, dropped) == (1, 1)
    again, r2, d2 = remap_layout(new, chunks)
    assert again == new and (r2, d2) == (0, 0)  # idempotent


@pytest.mark.asyncio
async def test_plan_layout_statuses(migrated_db: aiosqlite.Connection) -> None:
    conn = migrated_db
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path, layout_json) VALUES "
        "(1, 'sha256:a', 'pdf_upload', 'a', 'A', '/a', '/a', '/a', ?), "
        "(2, 'sha256:b', 'pdf_upload', 'b', 'B', '/b', '/b', '/b', NULL)",
        (json.dumps([{"label": "Figure 1", "caption": "Fig. 1 | Cells", "chunk_id": 5}]),),
    )
    await conn.execute(
        "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
        "VALUES (900, 1, 'S', 0, 10, 'Fig. 1 | Cells under phase contrast')")
    await conn.commit()
    plan = await plan_layout(conn, 1)
    assert plan.status == "updated" and plan.layout == [
        {"label": "Figure 1", "caption": "Fig. 1 | Cells", "chunk_id": 900}]
    assert (await plan_layout(conn, 2)).status == "no_layout"
