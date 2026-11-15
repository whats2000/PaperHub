from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub import settings_overlay as ov
from paperhub.app import apply_settings_overlay_at_boot, create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio

LOCKED = "PAPERHUB_OUTBOUND_REVIEW"


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    monkeypatch.setenv("PAPERHUB_LOCKED_SETTINGS", f"nope, {LOCKED} ,PAPERHUB_OUTBOUND_UNSURE")
    monkeypatch.setenv(LOCKED, "1")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
    ov.reset_for_tests()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    ov.reset_for_tests()


async def _rows(tmp_path: Path) -> dict[str, str]:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn, conn.execute(
        "SELECT key, value FROM settings"
    ) as cur:
        return {r[0]: r[1] for r in await cur.fetchall()}


async def test_get_marks_locked_fields(client: AsyncClient) -> None:
    body = (await client.get("/settings")).json()
    fields = {f["key"]: f for c in body["categories"] for f in c["fields"]}
    assert fields[LOCKED]["locked"] is True
    assert fields["PAPERHUB_OUTBOUND_ALLOWLIST"]["locked"] is False


async def test_patch_locked_key_403(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = await client.patch("/settings", json={LOCKED: "0"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "locked", "keys": [LOCKED]}
    import os

    assert os.environ[LOCKED] == "1"
    assert LOCKED not in await _rows(tmp_path)


async def test_patch_clearing_locked_key_403(client: AsyncClient) -> None:
    resp = await client.patch("/settings", json={LOCKED: None})
    assert resp.status_code == 403


async def test_patch_mixed_locked_and_free_rejects_whole_patch(
    client: AsyncClient, tmp_path: Path
) -> None:
    resp = await client.patch(
        "/settings", json={LOCKED: "0", "PAPERHUB_PAPER_QA_MAX_SECTION_READS": "12"}
    )
    assert resp.status_code == 403
    assert await _rows(tmp_path) == {}


async def test_unlocked_keys_still_patch(client: AsyncClient) -> None:
    resp = await client.patch("/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": "12"})
    assert resp.status_code == 200
    assert resp.json()["updated"] == ["PAPERHUB_PAPER_QA_MAX_SECTION_READS"]


async def test_overlay_skips_locked_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setenv("PAPERHUB_LOCKED_SETTINGS", LOCKED)
    monkeypatch.setenv(LOCKED, "1")
    ov.reset_for_tests()
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, '0', datetime('now'))",
            (LOCKED,),
        )
        await conn.commit()
        await apply_settings_overlay_at_boot(conn)
    assert os.environ[LOCKED] == "1"
    ov.reset_for_tests()


async def test_locked_key_is_default_ignores_stale_db_row(
    client: AsyncClient, tmp_path: Path
) -> None:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES ('PAPERHUB_OUTBOUND_UNSURE', 'block', datetime('now'))"
        )
        await conn.commit()
    body = (await client.get("/settings")).json()
    fields = {f["key"]: f for c in body["categories"] for f in c["fields"]}
    assert fields["PAPERHUB_OUTBOUND_UNSURE"]["is_default"] is True
    assert fields[LOCKED]["is_default"] is False  # env pins "1", default is "0"
