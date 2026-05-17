"""Typed Settings singleton — all env-derived config flows through here (NFR-04, NFR-11)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PAPERHUB_",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    workspace_root: Path
    db_path: Path

    vector_backend: Literal["chroma", "sqlite-vec"] = "chroma"
    chroma_path: Path | None = None  # if None, defaults to workspace_root / "chroma" (Task 5)

    router_model: str = "claude-haiku-4-5"
    generation_model: str = "claude-sonnet-4-6"
    judge_model: str = "claude-haiku-4-5"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-base"

    # API keys: prefer the prefixed form (PAPERHUB_ANTHROPIC_API_KEY) but fall
    # back to the ecosystem-standard bare name (ANTHROPIC_API_KEY) via AliasChoices.
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERHUB_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERHUB_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERHUB_GEMINI_API_KEY", "GEMINI_API_KEY"),
    )

    ollama_base_url: str = "http://localhost:11434"

    mcp_arxiv_command: str = "uvx arxiv-mcp-server"
    mcp_filesystem_command: str = "npx -y @modelcontextprotocol/server-filesystem"
    grobid_url: str = "http://localhost:8070"


def get_settings() -> Settings:
    # Load .env into os.environ so downstream libraries (LiteLLM reads provider keys
    # from os.environ directly: GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY)
    # see what the user wrote in .env. `override=False` means existing shell-set
    # env vars (e.g. those set by pytest's monkeypatch.setenv) still win — safe
    # for tests.
    load_dotenv(override=False)

    # pydantic-settings resolves required fields from env vars, not kwargs.
    # This version of pydantic-settings has a mypy plugin that makes Settings()
    # valid without kwargs — no type: ignore needed.
    settings = Settings()

    # Export typed API keys back to os.environ so LiteLLM (which reads via
    # os.environ.get) picks them up uniformly whether they came from .env, the
    # shell, or PAPERHUB_-prefixed aliases. No-op if the key is None.
    for env_name, secret in (
        ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        ("OPENAI_API_KEY", settings.openai_api_key),
        ("GEMINI_API_KEY", settings.gemini_api_key),
    ):
        if secret is not None and env_name not in os.environ:
            os.environ[env_name] = secret.get_secret_value()

    return settings
