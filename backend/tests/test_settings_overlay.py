# backend/tests/test_settings_overlay.py
import os

import pytest

from paperhub import settings_overlay as ov


@pytest.fixture(autouse=True)
def _isolate_env():
    """Snapshot + restore os.environ and reset the overlay base between tests
    (the overlay mutates process-global os.environ, bypassing monkeypatch)."""
    before = dict(os.environ)
    ov.reset_for_tests()
    yield
    os.environ.clear()
    os.environ.update(before)
    ov.reset_for_tests()


def test_set_then_clear_reverts_to_unset() -> None:
    os.environ.pop("PAPERHUB_LOG_LEVEL", None)
    ov.set_override("PAPERHUB_LOG_LEVEL", "DEBUG")
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "DEBUG"
    ov.clear_override("PAPERHUB_LOG_LEVEL")
    assert "PAPERHUB_LOG_LEVEL" not in os.environ


def test_set_then_clear_reverts_to_env_value() -> None:
    os.environ["PAPERHUB_LOG_LEVEL"] = "INFO"  # simulate .env
    ov.set_override("PAPERHUB_LOG_LEVEL", "DEBUG")
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "DEBUG"
    ov.clear_override("PAPERHUB_LOG_LEVEL")
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "INFO"


def test_apply_overlay_records_base_before_applying() -> None:
    os.environ["PAPERHUB_MODEL_SMALL"] = "from-env"
    ov.apply_overlay({"PAPERHUB_MODEL_SMALL": "from-db"})
    assert os.environ["PAPERHUB_MODEL_SMALL"] == "from-db"
    ov.clear_override("PAPERHUB_MODEL_SMALL")
    assert os.environ["PAPERHUB_MODEL_SMALL"] == "from-env"


def test_literal_absent_string_value_is_restored_not_deleted() -> None:
    # A real env value equal to the old string sentinel must be restored.
    os.environ["PAPERHUB_LOG_LEVEL"] = "__absent__"
    ov.set_override("PAPERHUB_LOG_LEVEL", "DEBUG")
    ov.clear_override("PAPERHUB_LOG_LEVEL")
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "__absent__"
