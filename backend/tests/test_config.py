"""Tests for the typed Settings singleton."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperhub.config import Settings


def test_settings_defaults_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
    # Delete every env var Settings reads — this test verifies code-level defaults.
    # Sibling tests / fixtures may have called get_settings() which calls
    # load_dotenv() and pollutes os.environ with PAPERHUB_* keys; explicit delenv
    # ensures isolation regardless of test order.
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "PAPERHUB_ANTHROPIC_API_KEY",
        "PAPERHUB_OPENAI_API_KEY",
        "PAPERHUB_GEMINI_API_KEY",
        "PAPERHUB_ROUTER_MODEL",
        "PAPERHUB_GENERATION_MODEL",
        "PAPERHUB_JUDGE_MODEL",
        "PAPERHUB_EMBEDDING_MODEL",
        "PAPERHUB_RERANKER_MODEL",
        "PAPERHUB_VECTOR_BACKEND",
        "PAPERHUB_CHROMA_PATH",
        "PAPERHUB_OLLAMA_BASE_URL",
        "PAPERHUB_MCP_ARXIV_COMMAND",
        "PAPERHUB_MCP_FILESYSTEM_COMMAND",
        "PAPERHUB_GROBID_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    # `_env_file=None` disables .env file loading for this test (the developer's
    # local .env shouldn't affect the default-values test).
    s = Settings(_env_file=None)

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
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-789")

    s = Settings(_env_file=None)

    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-test-123"
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "sk-test-456"
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "gemini-test-789"
