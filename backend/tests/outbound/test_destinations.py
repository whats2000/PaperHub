"""Destination allowlist: the cheapest outbound checkpoint stage."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.outbound.destinations import (
    PUBLIC_WEB_SEARCH,
    OutboundBlocked,
    check_destination,
    list_destinations,
)

_ALLOW = "export.arxiv.org,arxiv.org,api.semanticscholar.org,api.unpaywall.org"


async def _init_db(path: Path) -> None:
    async with aiosqlite.connect(path) as conn:
        await apply_schema(conn)


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    for k in ("PAPERHUB_PROFILE", "PAPERHUB_OUTBOUND_ALLOWLIST",
              "PAPERHUB_TRUSTED_SEARCH_URL", "PAPERHUB_PUBLIC_WEB_SEARCH"):
        monkeypatch.delenv(k, raising=False)
    asyncio.run(_init_db(tmp_path / "paperhub.db"))
    return tmp_path


def _rows(ws: Path) -> list[tuple[str, ...]]:
    with sqlite3.connect(ws / "paperhub.db") as c:
        return c.execute(
            "SELECT actor, caller, kind, destination, sent_text, decision, "
            "stage, reason FROM outbound_log ORDER BY id"
        ).fetchall()


def _deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")


def test_allow_all_in_dev(ws: Path) -> None:
    check_destination("https://anything.example/x?q=1", caller="c", kind="k")
    assert _rows(ws) == []


def test_deployment_blocks_unlisted_host(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _deploy(monkeypatch)
    check_destination("https://export.arxiv.org/api/query", caller="c", kind="k")
    with pytest.raises(OutboundBlocked) as ei:
        check_destination("https://evil.example:8443/p?q=secret", caller="s2.x", kind="s2_search")
    assert ei.value.destination == "evil.example"
    assert ei.value.reason == "destination not allowed"
    # Sync caller, no event loop: the blocked row is written; the path/query
    # (which may carry user text) is not stored.
    assert _rows(ws) == [(
        "system", "s2.x", "s2_search", "evil.example", None, "blocked",
        "allowlist", "destination not allowed",
    )]
    # The sync path knows the row it wrote.
    with sqlite3.connect(ws / "paperhub.db") as c:
        assert ei.value.log_id == c.execute("SELECT MAX(id) FROM outbound_log").fetchone()[0]


def test_log_blocked_false_leaves_the_row_to_the_caller(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deploy(monkeypatch)
    with pytest.raises(OutboundBlocked) as ei:
        check_destination("https://evil.example/", caller="c", kind="k", log_blocked=False)
    assert ei.value.log_id is None
    assert _rows(ws) == []


def test_trusted_host_always_allowed(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _deploy(monkeypatch)
    monkeypatch.setenv("PAPERHUB_TRUSTED_SEARCH_URL", "https://Search.Internal:9000/q")
    check_destination("https://search.internal/anything", caller="c", kind="k")


def test_ui_set_trusted_url_cannot_widen_a_locked_allowlist(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R53: with the allowlist locked and the trusted URL not locked (a UI
    setting), the trusted host is not admitted on its own."""
    _deploy(monkeypatch)
    monkeypatch.setenv("PAPERHUB_LOCKED_SETTINGS", "PAPERHUB_OUTBOUND_ALLOWLIST")
    monkeypatch.setenv("PAPERHUB_TRUSTED_SEARCH_URL", "https://exfil.example/q")
    with pytest.raises(OutboundBlocked):
        check_destination("https://exfil.example/q?x=1", caller="c", kind="k")
    assert all(d["host"] != "exfil.example" for d in list_destinations())
    # Listed by the deployer: allowed as usual.
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", _ALLOW + ",exfil.example")
    check_destination("https://exfil.example/q", caller="c", kind="k")


def test_locked_trusted_url_is_admitted_with_a_locked_allowlist(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deploy(monkeypatch)
    monkeypatch.setenv(
        "PAPERHUB_LOCKED_SETTINGS",
        "PAPERHUB_OUTBOUND_ALLOWLIST,PAPERHUB_TRUSTED_SEARCH_URL")
    monkeypatch.setenv("PAPERHUB_TRUSTED_SEARCH_URL", "https://search.internal/q")
    check_destination("https://search.internal/q", caller="c", kind="k")
    assert {"host": "search.internal", "source": "trusted_search"} in list_destinations()


@pytest.mark.parametrize("url", [
    "https://evil.arxiv.org.attacker.com/x",
    "https://export.arxiv.org.evil.com/x",
    "https://www.arxiv.org/x",
    "https://attacker.com/arxiv.org",
    "not a url",
])
def test_subdomain_not_implied(ws: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _deploy(monkeypatch)
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "arxiv.org")
    with pytest.raises(OutboundBlocked):
        check_destination(url, caller="c", kind="k")


def test_host_match_ignores_case_and_port(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _deploy(monkeypatch)
    check_destination("https://ArXiv.ORG:443/pdf/1", caller="c", kind="k")


def test_public_web_search_follows_flag(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _deploy(monkeypatch)
    with pytest.raises(OutboundBlocked):
        check_destination(PUBLIC_WEB_SEARCH, caller="mcp.web", kind="web_search")
    monkeypatch.setenv("PAPERHUB_PUBLIC_WEB_SEARCH", "1")
    check_destination(PUBLIC_WEB_SEARCH, caller="mcp.web", kind="web_search")
    assert [r[3] for r in _rows(ws)] == [PUBLIC_WEB_SEARCH]


def test_log_failure_does_not_mask_the_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deploy(monkeypatch)
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path / "no-db-here"))
    with pytest.raises(OutboundBlocked):
        check_destination("https://evil.example/", caller="c", kind="k")


def test_list_destinations(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert {"host": "*", "source": "deployer"} in list_destinations()
    _deploy(monkeypatch)
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", _ALLOW + ",corp.example")
    monkeypatch.setenv("PAPERHUB_TRUSTED_SEARCH_URL", "https://search.internal:9000/q")
    monkeypatch.setenv("PAPERHUB_PUBLIC_WEB_SEARCH", "1")
    got = list_destinations()
    assert {"host": "arxiv.org", "source": "paper_source"} in got
    # Unpaywall: an open-access lookup by DOI, a paper source.
    assert {"host": "api.unpaywall.org", "source": "paper_source"} in got
    assert {"host": "corp.example", "source": "deployer"} in got
    assert {"host": "search.internal", "source": "trusted_search"} in got
    assert {"host": PUBLIC_WEB_SEARCH, "source": "public_search"} in got
    monkeypatch.setenv("PAPERHUB_PUBLIC_WEB_SEARCH", "0")
    assert all(d["source"] != "public_search" for d in list_destinations())


@pytest.mark.asyncio
async def test_destinations_endpoint(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    _deploy(monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        r = await c.get("/outbound/destinations")
    assert r.status_code == 200
    assert {"host": "export.arxiv.org", "source": "paper_source"} in r.json()


def test_search_destinations_follow_the_policy(
    ws: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The "Allow this search?" card names where a search goes (H5a wave 3)."""
    from paperhub.outbound.destinations import search_destinations

    monkeypatch.setenv("PAPERHUB_PUBLIC_WEB_SEARCH", "1")
    assert search_destinations() == ["public-web-search", "api.semanticscholar.org"]
    monkeypatch.setenv("PAPERHUB_PUBLIC_WEB_SEARCH", "0")
    assert search_destinations() == ["api.semanticscholar.org"]
    monkeypatch.setenv("PAPERHUB_TRUSTED_SEARCH_URL", "https://search.internal/q")
    assert search_destinations() == ["search.internal", "api.semanticscholar.org"]
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "arxiv.org")
    monkeypatch.delenv("PAPERHUB_TRUSTED_SEARCH_URL")
    assert search_destinations() == []
