from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub import settings_overlay as ov
from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.outbound import held, log

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await log.append(conn, actor="agent", caller="c", kind="web_search",
                         destination="arxiv.org", decision="sent",
                         stage="allowlist", sent_text="q")
        await held.create(conn, caller="c", kind="k", destination="web",
                          text="t", reason="r", unattended=True,
                          run_id=None, session_id=None)
    ov.reset_for_tests()
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    ov.reset_for_tests()


async def test_log_endpoints(client: AsyncClient) -> None:
    r = await client.get("/outbound/log", params={"decision": "sent"})
    assert r.status_code == 200
    assert r.json()[0]["destination"] == "arxiv.org"
    assert (await client.get("/outbound/log", params={"decision": "blocked"})
            ).json() == []
    c = await client.get("/outbound/log.csv")
    assert c.headers["content-type"].startswith("text/csv")
    assert c.text.splitlines()[0].startswith("id,created_at")


async def test_api_has_no_mutating_log_routes(client: AsyncClient) -> None:
    for method in ("DELETE", "PATCH", "PUT", "POST"):
        r = await client.request(method, "/outbound/log/1")
        assert r.status_code in (404, 405)
        r = await client.request(method, "/outbound/log")
        assert r.status_code == 405


async def test_held_count_endpoint(client: AsyncClient) -> None:
    assert (await client.get("/outbound/held/count")).json() == {"count": 1}
    rows = (await client.get("/outbound/held")).json()
    assert len(rows) == 1 and rows[0]["status"] == "pending"


async def test_bad_since_is_422(client: AsyncClient) -> None:
    for path in ("/outbound/log", "/outbound/log.csv"):
        r = await client.get(path, params={"since": "yesterday"})
        assert r.status_code == 422
        assert "since" in r.json()["detail"]


async def test_unrelated_value_error_is_not_a_422(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: object, **_k: object) -> list[object]:
        raise ValueError("internal bug")

    monkeypatch.setattr(log, "query", boom)
    with pytest.raises(ValueError, match="internal bug"):
        await client.get("/outbound/log")


async def test_trusted_search_status_not_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAPERHUB_TRUSTED_SEARCH_URL", raising=False)
    r = await client.get("/outbound/trusted-search/status")
    assert r.status_code == 200
    assert r.json() == {"reachable": False, "error": "not_configured"}
