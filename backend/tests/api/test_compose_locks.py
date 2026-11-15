"""The compose deployment's lock list (R53, SRS §III-10.5 H5a "Deployer lock
list"): protections, the allowlist, the trusted-search URL and every model
provider are env-only. Unset locked keys keep their effective default."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from paperhub import settings_overlay as ov
from paperhub.app import apply_settings_overlay_at_boot, create_app
from paperhub.db.migrate import apply_schema
from paperhub.outbound.destinations import OutboundBlocked, check_destination
from paperhub.settings_registry import SETTINGS_REGISTRY

_COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"

_MODEL_KEYS = {f.key for f in SETTINGS_REGISTRY
               if f.key.startswith("PAPERHUB_") and f.key.endswith("_MODEL")} | {
    "PAPERHUB_MODEL_SMALL", "PAPERHUB_MODEL_FLAGSHIP", "PAPERHUB_MARKER_LLM_MODEL"}


def _compose_env() -> dict[str, str]:
    data = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    env: dict[str, str] = {}
    for item in data["services"]["backend"]["environment"]:
        key, _, value = str(item).partition("=")
        env[key] = value
    return env


def _locked() -> set[str]:
    return {k.strip() for k in _compose_env()["PAPERHUB_LOCKED_SETTINGS"].split(",")}


def test_compose_locks_protections_allowlist_trusted_url_and_models() -> None:
    locked = _locked()
    assert {"PAPERHUB_OUTBOUND_REVIEW", "PAPERHUB_OUTBOUND_UNSURE",
            "PAPERHUB_OUTBOUND_ALLOWLIST", "PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS",
            "PAPERHUB_TRUSTED_SEARCH_URL"} <= locked
    assert "PAPERHUB_OUTBOUND_REVIEW_MODEL" in _MODEL_KEYS
    assert sorted(_MODEL_KEYS - locked) == []


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    for key in _MODEL_KEYS | {"PAPERHUB_TRUSTED_SEARCH_URL", "PAPERHUB_OUTBOUND_ALLOWLIST"}:
        monkeypatch.delenv(key, raising=False)
    for key, value in _compose_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    ov.reset_for_tests()
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        # Stale UI rows from before the lock: ignored for locked keys.
        for key, value in (("PAPERHUB_MODEL_SMALL", "openai/cloud-small"),
                           ("PAPERHUB_AGENT_MODEL", "openai/cloud-agent"),
                           ("PAPERHUB_TRUSTED_SEARCH_URL", "https://exfil.example/q")):
            await conn.execute(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))", (key, value))
        await conn.commit()
        await apply_settings_overlay_at_boot(conn)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    ov.reset_for_tests()


@pytest.mark.asyncio
async def test_unset_locked_model_keys_keep_their_default_and_show_locked(
    client: AsyncClient,
) -> None:
    assert "PAPERHUB_MODEL_SMALL" not in os.environ  # the stale row did not apply
    assert "PAPERHUB_AGENT_MODEL" not in os.environ
    body = (await client.get("/settings")).json()
    fields = {f["key"]: f for c in body["categories"] for f in c["fields"]}
    small = fields["PAPERHUB_MODEL_SMALL"]
    assert small["locked"] is True and small["is_default"] is True
    assert small["value"] == "gemini/gemini-3.1-flash-lite"
    agent = fields["PAPERHUB_AGENT_MODEL"]
    assert agent["locked"] is True and not agent["value"]
    # Unset: the slot falls back to its tier, as without a lock.
    from paperhub.config import load_settings
    assert load_settings().agent_model == "gemini/gemini-2.5-pro"
    for key in _MODEL_KEYS & fields.keys():
        assert fields[key]["locked"] is True, key
    assert fields["PAPERHUB_TRUSTED_SEARCH_URL"]["locked"] is True

    resp = await client.patch("/settings", json={"PAPERHUB_MODEL_FLAGSHIP": "openai/x"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_stale_trusted_url_row_cannot_widen_the_allowlist(client: AsyncClient) -> None:
    with pytest.raises(OutboundBlocked):
        check_destination("https://exfil.example/q", caller="c", kind="k")
