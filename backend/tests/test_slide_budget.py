"""Deck-length budget is no longer regex-parsed (that CJK-only parser couldn't
honor "pages" / ranges / durations and returned a wrong default that
contradicted the user's request). The outline now reads the requested length
straight from the task; the only knob here is the CONFIGURABLE fallback default
used when the request names no length.
"""
import os
from unittest import mock

from paperhub.config import load_settings


def test_default_slide_length_default_is_15() -> None:
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PAPERHUB_SLIDE_DEFAULT_LENGTH", None)
        assert load_settings().slide_default_length == 15


def test_default_slide_length_env_override() -> None:
    with mock.patch.dict(os.environ, {"PAPERHUB_SLIDE_DEFAULT_LENGTH": "22"}):
        assert load_settings().slide_default_length == 22
