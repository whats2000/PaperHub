"""H4 Task 11: the core agent's settings."""
from __future__ import annotations

import pytest

from paperhub.config import flagship_tier_model, load_settings, small_tier_model
from paperhub.settings_registry import SETTINGS_REGISTRY

_ENVS = [
    "PAPERHUB_AGENT_MODEL", "PAPERHUB_AGENT_GATHER_MODEL",
    "PAPERHUB_AGENT_AUTOAPPROVE_MODEL", "PAPERHUB_AGENT_MAX_STEPS",
    "PAPERHUB_AGENT_MAX_TURN_TOKENS", "PAPERHUB_AGENT_KEEP_TURNS",
]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for e in _ENVS:
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))


def test_defaults() -> None:
    s = load_settings()
    assert s.agent_model == flagship_tier_model()
    assert s.agent_gather_model == small_tier_model()
    assert s.agent_autoapprove_model == small_tier_model()
    assert s.agent_max_steps == 12
    assert s.agent_max_turn_tokens == 120000
    assert s.agent_keep_turns == 12


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_AGENT_MODEL", "m/a")
    monkeypatch.setenv("PAPERHUB_AGENT_GATHER_MODEL", "m/b")
    monkeypatch.setenv("PAPERHUB_AGENT_AUTOAPPROVE_MODEL", "m/c")
    monkeypatch.setenv("PAPERHUB_AGENT_MAX_STEPS", "5")
    monkeypatch.setenv("PAPERHUB_AGENT_MAX_TURN_TOKENS", "5000")
    monkeypatch.setenv("PAPERHUB_AGENT_KEEP_TURNS", "3")
    s = load_settings()
    assert (s.agent_model, s.agent_gather_model, s.agent_autoapprove_model) == (
        "m/a", "m/b", "m/c")
    assert (s.agent_max_steps, s.agent_max_turn_tokens, s.agent_keep_turns) == (
        5, 5000, 3)


def test_registry_has_fields() -> None:
    keys = {f.key for f in SETTINGS_REGISTRY}
    assert set(_ENVS) <= keys


@pytest.mark.parametrize("bad", ["-1", "0", "abc", ""])
def test_non_positive_tunables_fall_back(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    for name in ("PAPERHUB_AGENT_MAX_STEPS", "PAPERHUB_AGENT_MAX_TURN_TOKENS",
                 "PAPERHUB_AGENT_KEEP_TURNS"):
        monkeypatch.setenv(name, bad)
    s = load_settings()
    assert (s.agent_max_steps, s.agent_max_turn_tokens, s.agent_keep_turns) == (
        12, 120000, 12)


def test_runtime_overlay_value_is_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_AGENT_MAX_STEPS", "7")  # as settings_overlay does
    assert load_settings().agent_max_steps == 7
