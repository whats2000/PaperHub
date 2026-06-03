"""F4.5 Phase 16: save_version copies deck.pdf into edit_history/<v>.pdf and
records it on the snapshot; restore_version copies it back to deck.pdf when
present."""
from __future__ import annotations

import json
from pathlib import Path

from paperhub.pipelines.slide_pipeline.history import VersionHistory


def test_save_version_copies_pdf_and_records_filename(tmp_path: Path) -> None:
    (tmp_path / "deck.tex").write_text("dummy", encoding="utf-8")
    (tmp_path / "deck.pdf").write_bytes(b"%PDF-1.4 fake pdf bytes\n")
    history = VersionHistory(tmp_path)
    version_id = history.save_version("dummy", "test", {})
    assert version_id is not None
    cached = tmp_path / "edit_history" / f"{version_id}.pdf"
    assert cached.exists()
    assert cached.read_bytes() == b"%PDF-1.4 fake pdf bytes\n"
    snap = json.loads(
        (tmp_path / "edit_history" / f"{version_id}.json").read_text(encoding="utf-8")
    )
    assert snap["pdf_filename"] == f"{version_id}.pdf"


def test_save_version_records_null_pdf_filename_when_pdf_missing(
    tmp_path: Path,
) -> None:
    """If deck.pdf isn't on disk yet (a status='error' run), pdf_filename is None."""
    history = VersionHistory(tmp_path)
    version_id = history.save_version("dummy", "test", {})
    assert version_id is not None
    snap = json.loads(
        (tmp_path / "edit_history" / f"{version_id}.json").read_text(encoding="utf-8")
    )
    assert snap["pdf_filename"] is None


def test_restore_version_copies_cached_pdf_back(tmp_path: Path) -> None:
    """When the snapshot has a cached PDF, restore_version copies it to deck.pdf."""
    (tmp_path / "deck.pdf").write_bytes(b"original pdf")
    history = VersionHistory(tmp_path)
    version_id = history.save_version("v1 tex", "first compile", {})
    assert version_id is not None
    # Overwrite deck.pdf with new content + a new version.
    (tmp_path / "deck.pdf").write_bytes(b"v2 pdf")
    history.save_version("v2 tex", "second compile", {})
    # Restoring v1 should bring the original bytes back.
    ok = history.restore_version(f"{version_id}.json", str(tmp_path / "deck.tex"))
    assert ok
    assert (tmp_path / "deck.pdf").read_bytes() == b"original pdf"


def test_restore_version_skips_pdf_swap_when_legacy_snapshot(tmp_path: Path) -> None:
    """A legacy snapshot without pdf_filename leaves deck.pdf alone; the caller
    falls back to recompiling (this is just the helper's contract)."""
    history = VersionHistory(tmp_path)
    # Hand-write a legacy snapshot (no pdf_filename field).
    eh = tmp_path / "edit_history"
    eh.mkdir(parents=True, exist_ok=True)
    (eh / "version_legacy.json").write_text(
        json.dumps(
            {
                "tex_content": "x",
                "speaker_notes": None,
                "description": "legacy",
                "timestamp": "20260101_000000",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "deck.pdf").write_bytes(b"current pdf")
    ok = history.restore_version("version_legacy.json", str(tmp_path / "deck.tex"))
    assert ok
    assert (tmp_path / "deck.pdf").read_bytes() == b"current pdf"  # untouched
