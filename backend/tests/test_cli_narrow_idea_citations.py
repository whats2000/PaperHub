"""QA I5 migration: stored ideas' whole-section citations are narrowed."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from paperhub.cli.narrow_idea_citations import migrate
from paperhub.db.migrate import apply_schema
from paperhub.ideation.models import CitedField, MechanismCard

_FILLER = [f"Reference data acquisition step {i} with normalisation and alignment."
           for i in range(8)]
_UNET = ["The encoder downsamples the sequence through downres blocks.",
         "Upsampling blocks add skip connections from the matching encoder stage."]


async def _seed(db: Path) -> tuple[int, list[int]]:
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        cur = await conn.execute(
            "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
            "source_dir_path, html_path) VALUES ('pdf:1', 'pdf_upload', ?, 'AlphaGenome', "
            "'', '', '')", ("1" * 64,))
        pid = int(cur.lastrowid or 0)
        ids = []
        for k, text in enumerate([*_FILLER[:4], *_UNET, *_FILLER[4:]]):
            c = await conn.execute(
                "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
                "VALUES (?, 'Methods', ?, ?, ?)", (pid, k * 100, k * 100 + 90, text))
            ids.append(int(c.lastrowid or 0))
        card = MechanismCard(
            problem=CitedField(text="Long inputs are slow.", sections=["Methods"]),
            mechanism=CitedField(text="Downsample, process, upsample with skip connections.",
                                 sections=["Methods"]),
            domains=["Computational Biology"])
        await conn.execute(
            "INSERT INTO mechanism_cards (paper_content_id, card_json, digest_hash, model, "
            "slot) VALUES (?, ?, 'h', 'm', 's')", (pid, card.model_dump_json()))
        await conn.execute(
            "INSERT INTO ideas (source_doc, source_section, source_chunk_ids_json, title, "
            "rationale) VALUES (?, 'Methods', ?, 'Downsample with skip connections', '')",
            (pid, json.dumps(ids)))
        await conn.commit()
    return pid, ids


async def _stored(db: Path) -> list[int]:
    async with aiosqlite.connect(db) as conn, conn.execute(
        "SELECT source_chunk_ids_json FROM ideas") as cur:
        row = await cur.fetchone()
    assert row is not None
    return [int(x) for x in json.loads(row[0])]


async def test_migration_narrows_then_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _pid, ids = await _seed(db)
    dry = await migrate(db, dry_run=True)
    assert [r["status"] for r in dry] == ["narrowed"]
    assert await _stored(db) == ids  # a dry run writes nothing
    rows = await migrate(db, dry_run=False)
    assert rows[0]["status"] == "narrowed" and rows[0]["source"] == [10, 2]
    assert await _stored(db) == ids[4:6]
    again = await migrate(db, dry_run=False)
    assert [r["status"] for r in again] == ["unchanged"]
