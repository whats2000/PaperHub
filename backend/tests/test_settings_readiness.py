import pytest

from paperhub import settings_readiness as sr
from paperhub.settings_registry import provider_for_credential_key


def _boom(msg: str):
    """An async stub that fails if ever called (asserts no ping happened)."""
    async def _fail(**_kwargs: object) -> object:
        raise AssertionError(msg)

    return _fail


def test_provider_for_credential_key_known_and_fallback() -> None:
    assert provider_for_credential_key("GEMINI_API_KEY") == "gemini"
    assert provider_for_credential_key("OPENAI_API_KEY") == "openai"
    assert provider_for_credential_key("TOGETHERAI_API_KEY") == "together_ai"
    assert provider_for_credential_key("PERPLEXITYAI_API_KEY") == "perplexity"
    # Unknown *_API_KEY falls back to the lowercased prefix.
    assert provider_for_credential_key("NOVELPROVIDER_API_KEY") == "novelprovider"
    # Non-key credentials (config-only) map to no provider.
    assert provider_for_credential_key("AZURE_API_BASE") is None


def test_configured_providers_dedups_and_skips_non_keys() -> None:
    providers = sr.configured_providers(
        ["GEMINI_API_KEY", "OPENAI_API_KEY", "AZURE_API_BASE"]
    )
    assert providers == ["gemini", "openai"]


@pytest.mark.asyncio
async def test_compute_readiness_not_ready_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sr._reset_cache_for_tests()
    # Default gate models are gemini/* — strip every Google key so they fail.
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "VERTEXAI_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    # Missing key short-circuits — no ping should be attempted.
    monkeypatch.setattr(
        sr.litellm, "acompletion", _boom("acompletion must not run when key absent")
    )
    result = await sr.compute_readiness([])
    assert result["ready"] is False
    assert result["credentials_set"] is False
    assert result["models"]["small"]["key_ok"] is False
    assert "GEMINI_API_KEY" in result["models"]["small"]["missing_keys"]


@pytest.mark.asyncio
async def test_compute_readiness_ready_when_ping_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sr._reset_cache_for_tests()
    monkeypatch.setenv("GEMINI_API_KEY", "x")

    async def ok(**_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(sr.litellm, "acompletion", ok)
    result = await sr.compute_readiness(["GEMINI_API_KEY"])
    assert result["ready"] is True
    assert result["credentials_set"] is True
    assert result["models"]["flagship"]["key_ok"] is True


@pytest.mark.asyncio
async def test_empty_key_is_reported_missing_not_pinged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty GEMINI_API_KEY (LiteLLM calls it "present") must be named as
    missing — and short-circuit, never reaching the ping."""
    sr._reset_cache_for_tests()
    monkeypatch.setenv("GEMINI_API_KEY", "")  # present-but-empty
    monkeypatch.setattr(
        sr.litellm, "acompletion", _boom("must not ping an empty-key model")
    )
    result = await sr.compute_readiness([])
    assert result["ready"] is False
    assert result["models"]["small"]["key_ok"] is False
    assert result["models"]["small"]["missing_keys"] == ["GEMINI_API_KEY"]


@pytest.mark.asyncio
async def test_compute_readiness_not_ready_when_ping_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/invalid key or bad model id is present-but-broken — the ping
    catches it even though validate_environment reports the key as present."""
    sr._reset_cache_for_tests()
    monkeypatch.setenv("GEMINI_API_KEY", "x")

    async def boom(**_kwargs: object) -> object:
        raise RuntimeError("Missing Gemini API key")

    monkeypatch.setattr(sr.litellm, "acompletion", boom)
    result = await sr.compute_readiness(["GEMINI_API_KEY"])
    assert result["ready"] is False
    assert result["models"]["small"]["key_ok"] is False
    assert result["models"]["small"]["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_fetch_model_options_normalizes_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sr._reset_cache_for_tests()
    calls = {"n": 0}

    def fake_valid_models(**_kwargs: object) -> list[str]:
        calls["n"] += 1
        # Mix bare + prefixed ids — the helper must normalize both to prefixed.
        return ["gemini/gemini-2.5-pro", "gemini-3.1-flash-lite"]

    monkeypatch.setattr(sr.litellm, "get_valid_models", fake_valid_models)

    first = await sr.fetch_model_options(["gemini"])
    assert first["gemini"] == ["gemini/gemini-2.5-pro", "gemini/gemini-3.1-flash-lite"]
    # Second call within TTL is served from cache (no extra live fetch).
    second = await sr.fetch_model_options(["gemini"])
    assert second == first
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_fetch_model_options_falls_back_to_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sr._reset_cache_for_tests()
    monkeypatch.setattr(
        sr.litellm, "get_valid_models", lambda **_k: []
    )  # live yields nothing
    monkeypatch.setattr(
        sr.litellm, "models_by_provider", {"gemini": ["gemini/gemini-2.5-pro"]}
    )
    out = await sr.fetch_model_options(["gemini"])
    assert out["gemini"] == ["gemini/gemini-2.5-pro"]


@pytest.mark.asyncio
async def test_agent_model_is_gated_with_a_tool_calling_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review #10: every chat turn runs on the core agent model, so readiness
    checks it (PAPERHUB_AGENT_MODEL, else the flagship) and with a tools list:
    a model without tool calling fails the gate."""
    sr._reset_cache_for_tests()
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("PAPERHUB_MODEL_FLAGSHIP", "gemini/flag")
    monkeypatch.setenv("PAPERHUB_AGENT_MODEL", "gemini/no-tools")
    calls: list[dict[str, object]] = []

    async def ping(**kw: object) -> object:
        calls.append(kw)
        if kw["model"] == "gemini/no-tools" and kw.get("tools"):
            raise sr.litellm.BadRequestError(
                message="this model does not support tools", model="no-tools",
                llm_provider="gemini")
        return object()

    monkeypatch.setattr(sr.litellm, "acompletion", ping)
    result = await sr.compute_readiness(["GEMINI_API_KEY"])
    agent = result["models"]["agent"]
    assert agent["model"] == "gemini/no-tools"
    assert agent["key_ok"] is False and agent["error"] == "ToolCallingUnsupported"
    assert result["ready"] is False
    assert any(c["model"] == "gemini/no-tools" and c.get("tools") for c in calls)

    sr._reset_cache_for_tests()
    monkeypatch.delenv("PAPERHUB_AGENT_MODEL")
    result = await sr.compute_readiness(["GEMINI_API_KEY"])
    assert result["models"]["agent"]["model"] == "gemini/flag"
    assert result["models"]["agent"]["key_ok"] is True and result["ready"] is True


@pytest.mark.asyncio
async def test_transient_failure_of_the_tools_ping_is_not_tool_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-review N3: a timeout / 429 on the tools ping keeps its own class
    (transient, never blocking), not ToolCallingUnsupported."""
    sr._reset_cache_for_tests()
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("PAPERHUB_AGENT_MODEL", "gemini/agent")

    async def ping(**kw: object) -> object:
        if kw.get("tools"):
            raise sr.litellm.Timeout(message="slow", model="agent", llm_provider="gemini")
        return object()

    monkeypatch.setattr(sr.litellm, "acompletion", ping)
    result = await sr.compute_readiness(["GEMINI_API_KEY"])
    assert result["models"]["agent"]["error"] == "Timeout"
