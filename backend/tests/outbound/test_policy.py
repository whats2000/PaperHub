import pytest

from paperhub.outbound.policy import load_policy, locked_keys
from paperhub.settings_registry import parse_string_list


def test_dev_profile_defaults_match_today() -> None:
    p = load_policy({})
    assert p.review_enabled is False
    assert p.public_web_search is True
    assert p.allowlist is None
    assert p.private_upload_default is False
    assert p.unsure == "ask"
    assert p.log_retention_days == 365


def test_deployment_profile_defaults() -> None:
    p = load_policy({"PAPERHUB_PROFILE": "deployment"})
    assert p.review_enabled is True
    assert p.unsure == "ask"
    assert p.public_web_search is False
    assert p.allowlist == {"export.arxiv.org", "arxiv.org", "api.semanticscholar.org", "api.unpaywall.org"}
    assert p.private_upload_default is True


def test_explicit_env_beats_profile_default() -> None:
    p = load_policy({
        "PAPERHUB_PROFILE": "deployment",
        "PAPERHUB_OUTBOUND_REVIEW": "0",
        "PAPERHUB_PUBLIC_WEB_SEARCH": "1",
        "PAPERHUB_OUTBOUND_ALLOWLIST": "*",
        "PAPERHUB_OUTBOUND_REVIEW_MODEL": "ollama/x",
    })
    assert p.review_enabled is False
    assert p.public_web_search is True
    assert p.allowlist is None
    assert p.review_model == "ollama/x"


def test_review_model_defaults_to_small_tier() -> None:
    p = load_policy({"PAPERHUB_MODEL_SMALL": "ollama/small"})
    assert p.review_model == "ollama/small"


def test_parse_string_list() -> None:
    assert parse_string_list("KESTREL, project osprey\nKestrel") == (
        "KESTREL",
        "project osprey",
    )


def test_empty_values_fall_back_to_profile_default() -> None:
    p = load_policy({
        "PAPERHUB_PROFILE": "deployment",
        "PAPERHUB_OUTBOUND_ALLOWLIST": "",
        "PAPERHUB_OUTBOUND_REVIEW": "  ",
        "PAPERHUB_PUBLIC_WEB_SEARCH": "",
    })
    assert p.allowlist == {"export.arxiv.org", "arxiv.org", "api.semanticscholar.org", "api.unpaywall.org"}
    assert p.review_enabled is True
    assert p.public_web_search is False


def test_empty_values_under_dev_keep_dev_defaults() -> None:
    p = load_policy({"PAPERHUB_OUTBOUND_ALLOWLIST": "", "PAPERHUB_OUTBOUND_REVIEW": ""})
    assert p.allowlist is None
    assert p.review_enabled is False


def test_malformed_numbers_fall_back() -> None:
    p = load_policy({
        "PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS": "abc",
        "PAPERHUB_OUTBOUND_REVIEW_TIMEOUT": "x",
    })
    assert p.log_retention_days == 365
    assert p.review_timeout_s == 8.0
    assert load_policy({"PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS": "2"}).log_retention_days == 7


def test_unsure_parse() -> None:
    assert load_policy({"PAPERHUB_OUTBOUND_UNSURE": " Block "}).unsure == "block"
    assert load_policy({"PAPERHUB_OUTBOUND_UNSURE": "weird"}).unsure == "ask"
    assert load_policy({}).unsure == "ask"


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "0", "-3"])
def test_review_timeout_must_be_finite_positive(bad: str) -> None:
    assert load_policy({"PAPERHUB_OUTBOUND_REVIEW_TIMEOUT": bad}).review_timeout_s == 8.0


def test_review_timeout_valid_value_kept() -> None:
    assert load_policy({"PAPERHUB_OUTBOUND_REVIEW_TIMEOUT": "2.5"}).review_timeout_s == 2.5


def test_locked_keys_parsing() -> None:
    assert locked_keys({"PAPERHUB_LOCKED_SETTINGS": " A, B ,,A "}) == {"A", "B"}
    assert locked_keys({}) == frozenset()
