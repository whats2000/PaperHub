from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub import settings_overlay as ov
from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def settings_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncClient:
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
    ov.reset_for_tests()
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    ov.reset_for_tests()


async def test_get_settings_returns_categories(settings_client: AsyncClient) -> None:
    resp = await settings_client.get("/settings")
    assert resp.status_code == 200
    body = resp.json()
    cats = {c["key"] for c in body["categories"]}
    assert {"models_providers", "agents_memory", "integrations", "system"} <= cats


async def test_get_settings_masks_secret_value(settings_client: AsyncClient) -> None:
    resp = await settings_client.get("/settings")
    fields = [
        f
        for c in resp.json()["categories"]
        for f in c["fields"]
        if f["key"] == "PAPERHUB_SEMANTIC_SCHOLAR_API_KEY"
    ]
    assert fields and fields[0]["secret"] is True
    assert "value" not in fields[0]  # secret value never returned
    assert "is_set" in fields[0]


async def test_patch_hot_applies_non_secret(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch(
        "/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": "12"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "PAPERHUB_PAPER_QA_MAX_SECTION_READS" in body["updated"]
    # Hot-applied: load_settings reflects it immediately.
    from paperhub.config import load_settings
    assert load_settings().paper_qa_max_section_reads == 12
    # GET now reports it as non-default.
    got = await settings_client.get("/settings")
    field = [
        f for c in got.json()["categories"] for f in c["fields"]
        if f["key"] == "PAPERHUB_PAPER_QA_MAX_SECTION_READS"
    ][0]
    assert field["value"] == "12" and field["is_default"] is False


async def test_patch_flags_restart_required(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch("/settings", json={"PAPERHUB_LOG_LEVEL": "DEBUG"})
    assert resp.status_code == 200
    assert "PAPERHUB_LOG_LEVEL" in resp.json()["restart_required"]


async def test_patch_rejects_invalid_value(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch(
        "/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": "0"}
    )
    assert resp.status_code == 422


async def test_patch_rejects_unknown_key(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch("/settings", json={"PATH": "/evil"})
    assert resp.status_code == 422


async def test_patch_credential_is_write_only(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch("/settings", json={"OPENAI_API_KEY": "sk-secret"})
    assert resp.status_code == 200
    got = await settings_client.get("/settings")
    mp_cat = [c for c in got.json()["categories"] if c["key"] == "models_providers"][0]
    entry = [k for k in mp_cat["credentials"]["keys"] if k["key"] == "OPENAI_API_KEY"][0]
    assert entry["is_set"] is True
    assert "value" not in entry  # never echoed


async def test_patch_clear_reverts_to_default(settings_client: AsyncClient) -> None:
    await settings_client.patch("/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": "20"})
    resp = await settings_client.patch(
        "/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": None}
    )
    assert resp.status_code == 200
    assert "PAPERHUB_PAPER_QA_MAX_SECTION_READS" in resp.json()["cleared"]
    from paperhub.config import load_settings
    assert load_settings().paper_qa_max_section_reads == 8  # default restored


async def test_readiness_flips_after_adding_credential(
    settings_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperhub import settings_readiness as sr

    sr._reset_cache_for_tests()
    # Strip ambient Google keys so the gemini/* default gate models start invalid.
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "VERTEXAI_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    # The pre-flight ping is mocked to succeed so we test wiring, not network.
    async def ok(**_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(sr.litellm, "acompletion", ok)

    before = await settings_client.get("/settings/readiness")
    assert before.status_code == 200
    body = before.json()
    assert body["ready"] is False  # key absent -> short-circuit, no ping
    assert body["credentials_set"] is False
    assert body["models"]["small"]["key_ok"] is False

    # Adding the key hot-applies to os.environ; PATCH clears the readiness cache
    # so the next call re-pings (now succeeds) -> gate goes green.
    await settings_client.patch("/settings", json={"GEMINI_API_KEY": "x"})
    after = (await settings_client.get("/settings/readiness")).json()
    assert after["ready"] is True
    assert after["credentials_set"] is True


async def test_model_options_reports_configured_providers(
    settings_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperhub import settings_readiness as sr

    sr._reset_cache_for_tests()
    monkeypatch.setattr(sr.litellm, "get_valid_models", lambda **_k: ["openai/gpt-x"])
    await settings_client.patch("/settings", json={"OPENAI_API_KEY": "sk-x"})
    resp = await settings_client.get("/settings/model-options")
    assert resp.status_code == 200
    body = resp.json()
    assert "openai" in body["providers"]
    assert body["options"]["openai"] == ["openai/gpt-x"]
