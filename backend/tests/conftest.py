"""Shared pytest fixtures for the PaperHub backend test suite."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
