from httpx import ASGITransport, AsyncClient

import paperhub.api.version as version_mod
from paperhub.app import create_app


async def test_version_reports_current_and_no_check_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_UPDATE_CHECK", "0")
    version_mod._reset_cache_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["current"], str) and body["current"]
    assert body["latest"] is None
    assert body["update_available"] is False


async def test_version_reports_update_available_when_newer(monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_UPDATE_CHECK", "1")
    version_mod._reset_cache_for_tests()

    async def fake_fetch(repo: str):
        return ("999.0.0", f"https://github.com/{repo}/releases/tag/v999.0.0")

    monkeypatch.setattr(version_mod, "_fetch_latest_release", fake_fetch)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    body = resp.json()
    assert body["latest"] == "999.0.0"
    assert body["update_available"] is True
    assert body["html_url"].endswith("v999.0.0")


async def test_version_swallows_fetch_errors(monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_UPDATE_CHECK", "1")
    version_mod._reset_cache_for_tests()

    async def boom(repo: str):
        raise RuntimeError("network down")

    monkeypatch.setattr(version_mod, "_fetch_latest_release", boom)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json()["latest"] is None
