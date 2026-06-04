"""Tests for Settings / load_settings() env-var wiring (SRS F4.3)."""
from __future__ import annotations

import pytest

from paperhub.config import load_settings

# ---------------------------------------------------------------------------
# 10. External lookup services — unpaywall_email
# ---------------------------------------------------------------------------


def test_unpaywall_email_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PAPERHUB_UNPAYWALL_EMAIL is set to a non-empty string, the setting
    carries that exact value."""
    monkeypatch.setenv("PAPERHUB_UNPAYWALL_EMAIL", "ops@example.com")
    assert load_settings().unpaywall_email == "ops@example.com"


def test_unpaywall_email_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PAPERHUB_UNPAYWALL_EMAIL is absent from the environment, the
    setting is None (Unpaywall fallback is skipped)."""
    monkeypatch.delenv("PAPERHUB_UNPAYWALL_EMAIL", raising=False)
    assert load_settings().unpaywall_email is None


def test_unpaywall_email_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PAPERHUB_UNPAYWALL_EMAIL is set to an empty string (the common
    docker-compose / .env ``KEY=`` form), it is coerced to None so the
    dispatcher skips the call rather than sending an empty email param."""
    monkeypatch.setenv("PAPERHUB_UNPAYWALL_EMAIL", "")
    assert load_settings().unpaywall_email is None


# ---------------------------------------------------------------------------
# 11. Slide style profile — env-var wiring + legacy alias (F4.4 T8)
# ---------------------------------------------------------------------------


def test_slide_style_profile_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither env var set → the resolved profile name is ``"default"``
    (the new canonical name; the yaml registry ships it as the
    Final_Report gold methodology)."""
    monkeypatch.delenv("PAPERHUB_SLIDE_STYLE_PROFILE", raising=False)
    monkeypatch.delenv("PAPERHUB_SLIDE_THEME", raising=False)
    assert load_settings().slide_style_profile == "default"


def test_slide_style_profile_legacy_theme_gold_maps_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy ``PAPERHUB_SLIDE_THEME=gold`` env var (operators may
    still have it set in .env files / docker-compose) maps to the new
    canonical profile name ``"default"``."""
    monkeypatch.delenv("PAPERHUB_SLIDE_STYLE_PROFILE", raising=False)
    monkeypatch.setenv("PAPERHUB_SLIDE_THEME", "gold")
    assert load_settings().slide_style_profile == "default"


def test_slide_style_profile_legacy_theme_metropolis_maps_to_metropolis_minimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy ``PAPERHUB_SLIDE_THEME=metropolis`` → ``"metropolis_minimal"``."""
    monkeypatch.delenv("PAPERHUB_SLIDE_STYLE_PROFILE", raising=False)
    monkeypatch.setenv("PAPERHUB_SLIDE_THEME", "metropolis")
    assert load_settings().slide_style_profile == "metropolis_minimal"


def test_slide_style_profile_unknown_value_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd env var must NOT silently emit an unrelated style — the
    operator gets the safe default."""
    monkeypatch.delenv("PAPERHUB_SLIDE_STYLE_PROFILE", raising=False)
    monkeypatch.setenv("PAPERHUB_SLIDE_THEME", "metropolis_minimall")
    assert load_settings().slide_style_profile == "default"


def test_slide_style_profile_new_env_var_wins_over_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH are set, ``PAPERHUB_SLIDE_STYLE_PROFILE`` (the new
    preferred name) wins — the legacy alias is for ops who haven't
    migrated their config yet."""
    monkeypatch.setenv("PAPERHUB_SLIDE_STYLE_PROFILE", "metropolis_minimal")
    monkeypatch.setenv("PAPERHUB_SLIDE_THEME", "gold")
    assert load_settings().slide_style_profile == "metropolis_minimal"


def test_slide_style_profile_accepts_canonical_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new preferred env var accepts canonical profile names directly
    (no alias rewrite needed)."""
    monkeypatch.setenv("PAPERHUB_SLIDE_STYLE_PROFILE", "default")
    assert load_settings().slide_style_profile == "default"
    monkeypatch.setenv("PAPERHUB_SLIDE_STYLE_PROFILE", "metropolis_minimal")
    assert load_settings().slide_style_profile == "metropolis_minimal"


# ---------------------------------------------------------------------------
# 3. Tier defaults for LLM models — small vs flagship
# ---------------------------------------------------------------------------


def test_tier_defaults_unset_use_built_in_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars set: small-tier slots resolve to the built-in flash-lite
    string; flagship-tier slots resolve to the built-in 2.5-pro string."""
    for name in (
        "PAPERHUB_MODEL_SMALL", "PAPERHUB_MODEL_FLAGSHIP",
        "PAPERHUB_ROUTER_MODEL", "PAPERHUB_CHITCHAT_MODEL",
        "PAPERHUB_PAPER_QA_MODEL", "PAPERHUB_PAPER_QA_SUBAGENT_MODEL",
        "PAPERHUB_SQL_AGENT_MODEL", "PAPERHUB_SQL_ANSWER_MODEL",
        "PAPERHUB_REPORT_PLAN_MODEL", "PAPERHUB_REPORT_SECTION_MODEL",
        "PAPERHUB_REPORT_NOTES_MODEL", "PAPERHUB_REPORT_RESOLVE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    # Small-tier slots
    assert s.router_model == "gemini/gemini-3.1-flash-lite"
    assert s.chitchat_model == "gemini/gemini-3.1-flash-lite"
    assert s.paper_qa_subagent_model == "gemini/gemini-3.1-flash-lite"
    assert s.sql_agent_model == "gemini/gemini-3.1-flash-lite"
    assert s.report_resolve_model == "gemini/gemini-3.1-flash-lite"
    # Flagship-tier slots
    assert s.paper_qa_model == "gemini/gemini-2.5-pro"
    assert s.sql_answer_model == "gemini/gemini-2.5-pro"
    assert s.report_plan_model == "gemini/gemini-2.5-pro"
    assert s.report_section_model == "gemini/gemini-2.5-pro"
    assert s.report_notes_model == "gemini/gemini-2.5-pro"


def test_small_tier_env_var_changes_all_small_tier_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PAPERHUB_MODEL_SMALL`` is the default for every small-tier slot."""
    for name in (
        "PAPERHUB_ROUTER_MODEL", "PAPERHUB_CHITCHAT_MODEL",
        "PAPERHUB_PAPER_QA_SUBAGENT_MODEL",
        "PAPERHUB_SQL_AGENT_MODEL", "PAPERHUB_REPORT_RESOLVE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPERHUB_MODEL_SMALL", "openai/gpt-4o-mini")
    s = load_settings()
    assert s.router_model == "openai/gpt-4o-mini"
    assert s.chitchat_model == "openai/gpt-4o-mini"
    assert s.paper_qa_subagent_model == "openai/gpt-4o-mini"
    assert s.sql_agent_model == "openai/gpt-4o-mini"
    assert s.report_resolve_model == "openai/gpt-4o-mini"


def test_flagship_tier_env_var_changes_all_flagship_tier_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PAPERHUB_MODEL_FLAGSHIP`` is the default for every flagship-tier slot."""
    for name in (
        "PAPERHUB_PAPER_QA_MODEL", "PAPERHUB_SQL_ANSWER_MODEL",
        "PAPERHUB_REPORT_PLAN_MODEL", "PAPERHUB_REPORT_SECTION_MODEL",
        "PAPERHUB_REPORT_NOTES_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPERHUB_MODEL_FLAGSHIP", "anthropic/claude-opus-4-7")
    s = load_settings()
    assert s.paper_qa_model == "anthropic/claude-opus-4-7"
    assert s.sql_answer_model == "anthropic/claude-opus-4-7"
    assert s.report_plan_model == "anthropic/claude-opus-4-7"
    assert s.report_section_model == "anthropic/claude-opus-4-7"
    assert s.report_notes_model == "anthropic/claude-opus-4-7"


def test_specific_override_wins_over_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Specific slot env vars (e.g. ``PAPERHUB_ROUTER_MODEL``) still take
    precedence over the tier default, so operators can pin one slot
    independently."""
    monkeypatch.setenv("PAPERHUB_MODEL_SMALL", "openai/gpt-4o-mini")
    monkeypatch.setenv("PAPERHUB_ROUTER_MODEL", "gemini/gemini-3.1-flash-lite")
    monkeypatch.delenv("PAPERHUB_CHITCHAT_MODEL", raising=False)
    s = load_settings()
    # Specific override wins for router
    assert s.router_model == "gemini/gemini-3.1-flash-lite"
    # Tier still applies to the un-overridden chitchat slot
    assert s.chitchat_model == "openai/gpt-4o-mini"


def test_memory_conflict_model_defaults_to_small_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory.add's conflict-detection LLM is now a proper Settings field
    (was previously read directly from env in two places, bypassing config)."""
    monkeypatch.delenv("PAPERHUB_MEMORY_CONFLICT_MODEL", raising=False)
    monkeypatch.delenv("PAPERHUB_MODEL_SMALL", raising=False)
    assert load_settings().memory_conflict_model == "gemini/gemini-3.1-flash-lite"


def test_memory_conflict_model_follows_small_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PAPERHUB_MEMORY_CONFLICT_MODEL", raising=False)
    monkeypatch.setenv("PAPERHUB_MODEL_SMALL", "openai/gpt-4o-mini")
    assert load_settings().memory_conflict_model == "openai/gpt-4o-mini"


def test_memory_conflict_model_specific_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_MODEL_SMALL", "openai/gpt-4o-mini")
    monkeypatch.setenv("PAPERHUB_MEMORY_CONFLICT_MODEL", "pinned/model")
    assert load_settings().memory_conflict_model == "pinned/model"
