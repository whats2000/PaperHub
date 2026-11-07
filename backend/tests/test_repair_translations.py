"""paperhub.cli.repair_translations: echo purge, stale PDF rows, language aliases."""

from pathlib import Path

import aiosqlite
import pytest

from paperhub.cli.repair_translations import apply, plan
from paperhub.pipelines.paper_asset import BlockAsset, write_block_assets
from paperhub.translation.pdf_paragraphs import build_paragraphs
from paperhub.translation.placeholders import source_hash

ECHO = ("With the concept of photon introduced by Albert Einstein in 1905, the "
        "statistical description of light is necessary for the study of optics.")


async def _paper(conn: aiosqlite.Connection, source_dir: str) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path,"
        " source_dir_path, html_path, sha256) VALUES (?, 'pdf_upload', NULL, 'T', 's', ?, 'h', ?)",
        (source_dir, source_dir, source_dir),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def _row(conn: aiosqlite.Connection, pid: int, key: str, language: str,
               shash: str, text: str) -> None:
    await conn.execute(
        "INSERT INTO translations (paper_content_id, block_key, language, source_hash,"
        " memory_hash, text, model) VALUES (?, ?, ?, ?, 'm', ?, 'x')",
        (pid, key, language, shash, text),
    )
    await conn.commit()


async def _rows(conn: aiosqlite.Connection) -> set[tuple[str, str]]:
    async with conn.execute("SELECT block_key, language FROM translations") as cur:
        return {(str(r[0]), str(r[1])) for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_repair_plan_and_apply_is_idempotent(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    blocks = [
        BlockAsset("/page/0/Text/1", "Text", 0, (50, 100, 300, 140),
                   "<p>Shensheng Han, Hong Yu and Xia Shen</p>", None),
        BlockAsset("/page/0/Text/2", "Text", 0, (50, 150, 300, 200),
                   "<p>Ghost imaging obtains the image of an object.</p>", None),
    ]
    write_block_assets(tmp_path, blocks, inline_math_llm=True)
    pid = await _paper(migrated_db, str(tmp_path))
    [para] = build_paragraphs(blocks)  # the byline is skipped
    good = source_hash(para.text)

    await _row(migrated_db, pid, para.key, "繁體中文", good, "鬼成像取得物體的影像。")
    await _row(migrated_db, pid, "h:4", "繁體中文", "h", ECHO)  # echo
    await _row(migrated_db, pid, "h:4", "English", "h", ECHO)  # English target: fine
    await _row(migrated_db, pid, "p:/page/0/Text/1", "繁體中文", "x", "沈聖、于紅")  # skipped
    await _row(migrated_db, pid, para.key, "日本語", "old", "古い")  # source changed
    await _row(migrated_db, pid, "h:5", "Japanese", "h", "訳文")  # alias -> 日本語
    await _row(migrated_db, pid, "h:6", "Japanese", "h", "訳文")  # alias, taken
    await _row(migrated_db, pid, "h:6", "日本語", "h", "訳文")

    actions = await plan(migrated_db)
    kinds = sorted((a.kind, a.block_key, a.language) for a in actions)
    assert kinds == [
        ("alias_duplicate", "h:6", "Japanese"),
        ("alias_rekey", "h:5", "Japanese"),
        ("echo", "h:4", "繁體中文"),
        ("stale_pdf", "p:/page/0/Text/1", "繁體中文"),
        ("stale_pdf", para.key, "日本語"),
    ]
    await apply(migrated_db, actions)
    assert await _rows(migrated_db) == {
        (para.key, "繁體中文"), ("h:4", "English"), ("h:5", "日本語"), ("h:6", "日本語"),
    }
    assert await plan(migrated_db) == []


@pytest.mark.asyncio
async def test_plan_can_target_one_paper(migrated_db: aiosqlite.Connection,
                                         tmp_path: Path) -> None:
    a = await _paper(migrated_db, str(tmp_path / "a"))
    b = await _paper(migrated_db, str(tmp_path / "b"))
    await _row(migrated_db, a, "h:0", "繁體中文", "h", ECHO)
    await _row(migrated_db, b, "h:0", "繁體中文", "h", ECHO)
    assert [x.paper_content_id for x in await plan(migrated_db, b)] == [b]
