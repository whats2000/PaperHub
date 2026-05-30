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
