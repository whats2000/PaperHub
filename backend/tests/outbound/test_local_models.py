from paperhub.outbound.local_models import is_local_model, non_local_settings


def test_local_prefixes() -> None:
    for m in ("ollama/llama3", "ollama_chat/q", "hosted_vllm/x", "lm_studio/y", "llamafile/z"):
        assert is_local_model(m, {})


def test_openai_compatible_private_base_is_local() -> None:
    for base in ("http://10.1.2.3:8000/v1", "http://192.168.0.5/v1", "http://172.20.1.1/v1",
                 "http://localhost:11434/v1", "http://host.docker.internal:1234/v1",
                 "http://gpu.lan/v1", "http://llm.corp.internal/v1", "http://[::1]:80/v1"):
        assert is_local_model("openai/qwen", {"OPENAI_API_BASE": base}), base
    assert not is_local_model("openai/gpt-4o", {})
    assert not is_local_model("openai/gpt-4o", {"OPENAI_API_BASE": "https://api.openai.com/v1"})
    assert not is_local_model("openai/x", {"OPENAI_API_BASE": "http://172.32.0.1/v1"})


def test_gemini_is_not_local() -> None:
    assert not is_local_model("gemini/gemini-2.5-pro", {})
    assert not is_local_model("llama3", {})


def test_non_local_settings_resolves_effective_models() -> None:
    env = {"PAPERHUB_MODEL_SMALL": "ollama/a", "PAPERHUB_MODEL_FLAGSHIP": "ollama/b"}
    assert non_local_settings(env) == [("PAPERHUB_MARKER_LLM_MODEL",
                                        "gemini/gemini-3.1-flash-lite")]
    env["PAPERHUB_TRANSLATE_MODEL"] = "gemini/x"
    keys = dict(non_local_settings(env))
    assert keys["PAPERHUB_TRANSLATE_MODEL"] == "gemini/x"
    assert "PAPERHUB_ROUTER_MODEL" not in keys


def test_self_hosted_runtime_on_a_public_base_is_not_local() -> None:
    """A locked local model can be pointed elsewhere through its credential
    base URL: readiness must then warn (final review follow-on)."""
    assert is_local_model("ollama/llama3", {"OLLAMA_API_BASE": "http://10.0.0.5:11434"})
    assert is_local_model("ollama/llama3", {"OLLAMA_API_BASE": "http://host.docker.internal:11434"})
    assert not is_local_model("ollama/llama3", {"OLLAMA_API_BASE": "https://ollama.example.com"})
    assert not is_local_model("ollama_chat/q", {"OLLAMA_API_BASE": "http://8.8.8.8:11434"})
    assert not is_local_model("hosted_vllm/x", {"HOSTED_VLLM_API_BASE": "https://vllm.example.com/v1"})
    assert not is_local_model("lm_studio/y", {"LM_STUDIO_API_BASE": "https://lm.example.com/v1"})
    assert is_local_model("hosted_vllm/x", {"HOSTED_VLLM_API_BASE": "http://gpu.lan:8000/v1"})
