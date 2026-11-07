"""QA I8 migration: legacy near-duplicate domains fold into canonical fields."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from paperhub.cli.consolidate_domains import run
from paperhub.db.migrate import apply_schema


async def test_dry_run_then_fold(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
            "source_dir_path, html_path) VALUES ('pdf:1', 'pdf_upload', ?, 'Mamba', "
            "'', '', '')", ("1" * 64,))
        for name in ("Natural Language Processing", "Genomics", "Audio"):
            await conn.execute("INSERT INTO domains (name) VALUES (?)", (name,))
            await conn.execute(
                "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
                "SELECT 1, id, 0 FROM domains WHERE name = ?", (name,))
        await conn.commit()
    dry = await run(db, dry_run=True)
    assert dry["after"] == dry["before"] == {1: ["Natural Language Processing", "Genomics",
                                                "Audio"]}
    assert dry["capped"] == {"1": ["Audio"]}
    out = await run(db, dry_run=False)
    assert out["after"] == {1: ["Natural Language Processing", "Computational Biology"]}
    assert out["domains_in_use"] == 2
    again = await run(db, dry_run=False)
    assert again["renamed_or_merged"] == {} and again["capped"] == {}
