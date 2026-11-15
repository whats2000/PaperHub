from __future__ import annotations

import httpx
import pytest

from paperhub.outbound.trusted_search import trusted_search

URL = "https://search.example.org"


@pytest.fixture(autouse=True)
def _policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_TRUSTED_SEARCH_URL", URL)
    monkeypatch.setenv("PAPERHUB_PROFILE", "deployment")


async def test_trusted_search_parses_searxng_json() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"results": [
            {"title": "Mamba", "url": "https://arxiv.org/abs/2312.00752", "content": "SSM"},
            {"title": "B", "url": "https://x.org/b", "content": "c"},
        ]})

    meta: dict[str, object] = {}
    out = await trusted_search(
        "mamba", url=URL, max_results=1, meta=meta, transport=httpx.MockTransport(handler),
    )
    assert out == [{"title": "Mamba", "url": "https://arxiv.org/abs/2312.00752", "snippet": "SSM"}]
    assert seen[0].url.path == "/search"
    assert seen[0].url.params["q"] == "mamba" and seen[0].url.params["format"] == "json"
    assert meta["http_status"] == 200 and meta["count"] == 1


async def test_trusted_search_failure_returns_empty_and_records() -> None:
    meta: dict[str, object] = {}
    out = await trusted_search(
        "q", url=URL, meta=meta,
        transport=httpx.MockTransport(lambda r: httpx.Response(503, text="overloaded")),
    )
    assert out == [] and meta["http_status"] == 503 and meta["body_head"] == "overloaded"

    meta = {}
    out = await trusted_search(
        "q", url=URL, meta=meta,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")),
    )
    assert out == [] and meta["body_head"] == "<html>"


async def test_trusted_search_timeout_never_raises() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    meta: dict[str, object] = {}
    assert await trusted_search("q", url=URL, meta=meta, transport=httpx.MockTransport(boom)) == []
    assert "ConnectTimeout" in str(meta["error"])


async def test_trusted_search_malformed_url_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "*")
    for bad in ("http://host:abc", "http://[::1", "not a url", "http://"):
        meta: dict[str, object] = {}
        out = await trusted_search(
            "q", url=bad, meta=meta,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})),
        )
        assert out == [] and str(meta["error"]).startswith("invalid url"), bad


async def test_trusted_search_blocked_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "arxiv.org")
    hit: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hit.append(req)
        return httpx.Response(200, json={"results": []})

    meta: dict[str, object] = {}
    out = await trusted_search(
        "q", url="https://evil.example.com", meta=meta, transport=httpx.MockTransport(handler),
    )
    assert out == [] and str(meta["error"]).startswith("blocked") and hit == []


async def test_trusted_search_redirect_to_unlisted_host_blocked() -> None:
    contacted: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        contacted.append(req.url.host)
        if req.url.host == "search.example.org":
            return httpx.Response(302, headers={"location": "https://evil.example.com/search"})
        return httpx.Response(200, json={"results": []})

    meta: dict[str, object] = {}
    out = await trusted_search("q", url=URL, meta=meta, transport=httpx.MockTransport(handler))
    assert out == [] and str(meta["error"]).startswith("blocked")
    assert contacted == ["search.example.org"]


@pytest.mark.asyncio
async def test_probe_reachable_blocked_and_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    from paperhub.outbound.trusted_search import probe

    monkeypatch.delenv("PAPERHUB_PROFILE", raising=False)
    ok = httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))
    assert await probe("http://search.lab.internal", transport=ok) == {
        "reachable": True, "http_status": 200,
    }
    down = httpx.MockTransport(lambda req: httpx.Response(503))
    r = await probe("http://search.lab.internal", transport=down)
    assert r["reachable"] is False and r["http_status"] == 503

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    r = await probe("http://search.lab.internal", transport=httpx.MockTransport(boom))
    assert r["reachable"] is False and "ConnectError" in r["error"]
    assert await probe("") == {"reachable": False, "error": "not_configured"}
    assert await probe(None) == {"reachable": False, "error": "not_configured"}

    monkeypatch.setenv("PAPERHUB_OUTBOUND_ALLOWLIST", "arxiv.org")
    r = await probe("http://evil.example", transport=ok)
    assert r["reachable"] is False and r["error"].startswith("blocked")
