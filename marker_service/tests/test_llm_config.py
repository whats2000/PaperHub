from llm_config import marker_llm_config


def test_gemini_with_key():
    cfg = marker_llm_config(
        {"PAPERHUB_MARKER_LLM_MODEL": "gemini/gemini-x", "GEMINI_API_KEY": "k"}
    )
    assert cfg == {
        "llm_service": "marker.services.gemini.GoogleGeminiService",
        "gemini_api_key": "k",
        "gemini_model_name": "gemini-x",
    }


def test_gemini_google_key_fallback_and_default_model():
    cfg = marker_llm_config({"GOOGLE_API_KEY": "g"})
    assert cfg is not None
    assert cfg["gemini_api_key"] == "g"
    assert cfg["gemini_model_name"] == "gemini-3.1-flash-lite"


def test_defaults_to_model_small():
    cfg = marker_llm_config(
        {"PAPERHUB_MODEL_SMALL": "gemini/gemini-s", "GEMINI_API_KEY": "k"}
    )
    assert cfg is not None and cfg["gemini_model_name"] == "gemini-s"


def test_marker_model_overrides_small():
    cfg = marker_llm_config(
        {
            "PAPERHUB_MODEL_SMALL": "gemini/a",
            "PAPERHUB_MARKER_LLM_MODEL": "ollama/qwen2.5vl",
        }
    )
    assert cfg is not None and cfg["ollama_model"] == "qwen2.5vl"


def test_gemini_without_key_is_none():
    assert marker_llm_config({"PAPERHUB_MARKER_LLM_MODEL": "gemini/x"}) is None
    assert marker_llm_config({}) is None


def test_ollama_default_and_custom_base():
    cfg = marker_llm_config({"PAPERHUB_MARKER_LLM_MODEL": "ollama/m"})
    assert cfg == {
        "llm_service": "marker.services.ollama.OllamaService",
        "ollama_base_url": "http://localhost:11434",
        "ollama_model": "m",
    }
    cfg = marker_llm_config(
        {"PAPERHUB_MARKER_LLM_MODEL": "ollama/m", "OLLAMA_API_BASE": "http://h:1"}
    )
    assert cfg is not None and cfg["ollama_base_url"] == "http://h:1"


def test_openai_compatible():
    for prefix in ("openai", "hosted_vllm"):
        cfg = marker_llm_config(
            {
                "PAPERHUB_MARKER_LLM_MODEL": f"{prefix}/m",
                "OPENAI_API_BASE": "http://v/v1",
            }
        )
        assert cfg == {
            "llm_service": "marker.services.openai.OpenAIService",
            "openai_base_url": "http://v/v1",
            "openai_api_key": "none",
            "openai_model": "m",
        }
    cfg = marker_llm_config(
        {"PAPERHUB_MARKER_LLM_MODEL": "openai/m", "OPENAI_API_KEY": "sk"}
    )
    assert cfg is not None and cfg["openai_api_key"] == "sk"


def test_unknown_provider_none_and_warns(caplog):
    with caplog.at_level("WARNING"):
        assert marker_llm_config({"PAPERHUB_MARKER_LLM_MODEL": "anthropic/m"}) is None
    assert "anthropic/m" in caplog.text and "inline-math" in caplog.text


def test_non_gemini_small_is_not_inherited():
    """A non-gemini PAPERHUB_MODEL_SMALL (often text-only) falls back to Gemini."""
    cfg = marker_llm_config(
        {"PAPERHUB_MODEL_SMALL": "ollama/llama3", "GEMINI_API_KEY": "k"}
    )
    assert cfg is not None
    assert cfg["llm_service"] == "marker.services.gemini.GoogleGeminiService"
    assert cfg["gemini_model_name"] == "gemini-3.1-flash-lite"


def test_non_gemini_small_without_gemini_key_is_none():
    for small in ("ollama/llama3", "openai/gpt-x", "hosted_vllm/q"):
        assert marker_llm_config({"PAPERHUB_MODEL_SMALL": small}) is None


def test_openai_without_base_omits_base_url():
    cfg = marker_llm_config({"PAPERHUB_MARKER_LLM_MODEL": "openai/m"})
    assert cfg is not None
    assert "openai_base_url" not in cfg
