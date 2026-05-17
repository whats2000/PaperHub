"""Tests for the args redactor (NFR-09)."""

from __future__ import annotations

from pathlib import Path

from paperhub.tracing.redactor import redact


def test_redact_anthropic_api_key_in_string() -> None:
    out = redact({"prompt": "sk-ant-abc123xyz hello", "x": 1})
    assert "sk-ant-abc123xyz" not in str(out["prompt"])
    assert "<REDACTED:api-key>" in str(out["prompt"])
    assert out["x"] == 1


def test_redact_openai_api_key_in_string() -> None:
    out = redact({"k": "sk-proj-Abc123-_xyz"})
    assert "<REDACTED:api-key>" in str(out["k"])


def test_redact_absolute_home_directory_path() -> None:
    home_path = str(Path.home() / "secret" / "doc.pdf")
    out = redact({"path": home_path})
    assert "<REDACTED:home>" in str(out["path"])


def test_redact_nested_dict_and_list() -> None:
    out = redact({"outer": {"inner": ["sk-ant-zzz12345", "ok"]}})
    nested = out["outer"]
    assert isinstance(nested, dict)
    inner = nested["inner"]
    assert isinstance(inner, list)
    assert "<REDACTED:api-key>" in inner[0]
    assert inner[1] == "ok"
