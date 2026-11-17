"""H5b.1: the re-anchor step runs on a workspace made before the notes layer."""
from __future__ import annotations

from pathlib import Path

from paperhub.cli.reanchor_marks import migrate
from tests.cli._pre_notes import table_names


async def test_real_run_before_the_notes_layer_adds_its_tables(pre_notes_db: Path) -> None:
    assert await migrate(pre_notes_db, dry_run=False) == []
    assert "note_marks" in await table_names(pre_notes_db)


async def test_dry_run_before_the_notes_layer_changes_nothing(pre_notes_db: Path) -> None:
    before = await table_names(pre_notes_db)
    assert await migrate(pre_notes_db, dry_run=True) == []
    assert await table_names(pre_notes_db) == before
