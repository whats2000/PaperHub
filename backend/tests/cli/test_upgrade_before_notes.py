"""H5b.1: every upgrade step that can reach the notes tables brings the
schema up to date on a real run, so ``reingest_all_papers.ps1`` works on a
workspace the new backend has never started on (before the notes layer)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from tests.cli._pre_notes import table_names

_CLIS = [
    ("paperhub.cli.apply_schema", "paperhub-apply-schema"),
    ("paperhub.cli.reingest", "paperhub-reingest"),
    ("paperhub.cli.rerender_html", "paperhub-rerender-html"),
    ("paperhub.cli.repair_section_names", "paperhub-repair-section-names"),
]


@pytest.mark.parametrize(("module", "prog"), _CLIS)
async def test_a_real_run_adds_the_notes_tables(
    pre_notes_db: Path, monkeypatch: pytest.MonkeyPatch, module: str, prog: str,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(pre_notes_db.parent))
    monkeypatch.setattr(sys, "argv", [prog])
    await importlib.import_module(module)._amain()
    assert {"notes", "note_blocks", "note_marks"} <= await table_names(pre_notes_db)


@pytest.mark.parametrize(("module", "prog"), [c for c in _CLIS if c[1] != "paperhub-rerender-html"])
async def test_a_dry_run_changes_no_table(
    pre_notes_db: Path, monkeypatch: pytest.MonkeyPatch, module: str, prog: str,
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(pre_notes_db.parent))
    monkeypatch.setattr(sys, "argv", [prog, "--dry-run"])
    before = await table_names(pre_notes_db)
    await importlib.import_module(module)._amain()
    assert await table_names(pre_notes_db) == before
