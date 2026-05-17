"""Tests for the typed Settings singleton."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperhub.config import Settings


def test_settings_defaults_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PAPERHUB_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PAPERHUB_OPENAI_API_KEY", raising=False)

    s = Settings()

    assert s.workspace_root == tmp_path
    assert s.db_path == tmp_path / "paperhub.db"
    assert s.vector_backend == "chroma"
    assert s.router_model == "claude-haiku-4-5"
    assert s.generation_model == "claude-sonnet-4-6"
    assert s.judge_model == "claude-haiku-4-5"
    assert s.judge_model != s.generation_model, "judge must differ from generator (FR-12)"


def test_settings_api_keys_load_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-456")

    s = Settings()

    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-test-123"
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "sk-test-456"
