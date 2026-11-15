import importlib.util
from pathlib import Path

import pytest

from paperhub.outbound.local_models import marker_llm_model

_MARKER = Path(__file__).resolve().parents[3] / "marker_service" / "llm_config.py"

_MATRIX = [
    {},
    {"PAPERHUB_MODEL_SMALL": "gemini/gemini-2.5-flash"},
    {"PAPERHUB_MODEL_SMALL": "ollama/qwen"},
    {"PAPERHUB_MODEL_SMALL": "openai/gpt-4o-mini"},
    {"PAPERHUB_MARKER_LLM_MODEL": "hosted_vllm/vl"},
    {"PAPERHUB_MARKER_LLM_MODEL": "ollama/a", "PAPERHUB_MODEL_SMALL": "gemini/b"},
    {"PAPERHUB_MARKER_LLM_MODEL": "", "PAPERHUB_MODEL_SMALL": "gemini/b"},
]


@pytest.mark.skipif(not _MARKER.exists(), reason="marker_service/llm_config.py not present")
def test_backend_marker_model_matches_marker_service() -> None:
    spec = importlib.util.spec_from_file_location("_marker_llm_config", _MARKER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for env in _MATRIX:
        assert marker_llm_model(env) == mod.marker_llm_model(env), env
