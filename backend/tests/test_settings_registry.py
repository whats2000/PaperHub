# backend/tests/test_settings_registry.py
import pytest

from paperhub.api.settings import _CATEGORY_LABELS, _CATEGORY_ORDER
from paperhub.settings_registry import (
    SETTINGS_REGISTRY,
    coerce_value,
    field_by_key,
    is_allowed_credential_key,
)


def test_registry_covers_known_categories() -> None:
    categories = {f.category for f in SETTINGS_REGISTRY}
    assert {
        "llm_models",
        "agent_tunables",
        "memory",
        "external_services",
        "external_lookup",
        "storage",
        "logging",
        "marker",
        "slides",
    } <= categories


def test_dead_memory_semantic_is_not_in_registry() -> None:
    # PAPERHUB_MEMORY_SEMANTIC is dead config — omitted from the UI registry.
    assert field_by_key("PAPERHUB_MEMORY_SEMANTIC") is None


def test_int_field_rejects_out_of_range() -> None:
    field = field_by_key("PAPERHUB_PAPER_QA_MAX_SECTION_READS")
    assert field is not None
    with pytest.raises(ValueError):
        coerce_value(field, "0")  # min is 1
    assert coerce_value(field, "8") == "8"


def test_enum_field_rejects_unknown_choice() -> None:
    field = field_by_key("PAPERHUB_LOG_LEVEL")
    assert field is not None and field.restart_required is True
    with pytest.raises(ValueError):
        coerce_value(field, "TRACE")
    assert coerce_value(field, "DEBUG") == "DEBUG"


def test_email_field_validation() -> None:
    field = field_by_key("PAPERHUB_UNPAYWALL_EMAIL")
    assert field is not None
    with pytest.raises(ValueError):
        coerce_value(field, "not-an-email")
    assert coerce_value(field, "a@b.com") == "a@b.com"


def test_secret_field_is_marked() -> None:
    field = field_by_key("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY")
    assert field is not None and field.secret is True


def test_credential_allowlist() -> None:
    assert is_allowed_credential_key("GEMINI_API_KEY")
    assert is_allowed_credential_key("AZURE_API_BASE")
    assert is_allowed_credential_key("OPENROUTER_API_KEY")
    assert is_allowed_credential_key("SOME_NEW_PROVIDER_API_KEY")  # suffix match
    assert not is_allowed_credential_key("PATH")
    assert not is_allowed_credential_key("HOME")


def test_read_only_field_rejects_any_value() -> None:
    field = field_by_key("PAPERHUB_WORKSPACE")
    assert field is not None and field.read_only is True
    with pytest.raises(ValueError):
        coerce_value(field, "./other")


def test_string_field_rejects_empty() -> None:
    field = field_by_key("PAPERHUB_MODEL_SMALL")
    assert field is not None
    with pytest.raises(ValueError):
        coerce_value(field, "   ")


def test_paperhub_prefixed_keys_are_not_credentials() -> None:
    assert not is_allowed_credential_key("PAPERHUB_SECRET_KEY")
    assert not is_allowed_credential_key("PAPERHUB_API_KEY")


def test_every_registry_category_has_a_nav_slot() -> None:
    cats = {f.category for f in SETTINGS_REGISTRY} | {"provider_credentials"}
    assert cats == set(_CATEGORY_ORDER)
    assert cats == set(_CATEGORY_LABELS)
