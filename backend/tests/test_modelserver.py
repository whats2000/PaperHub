"""Tests for the isolated model-server process + HTTP clients.

Covers:
  - The FastAPI app's /embed and /rerank endpoints accept the wire
    contract (empty inputs short-circuit, normal inputs go through the
    embedder/reranker singletons).
  - The HTTP-client embedder/reranker serialise requests to the wire
    contract the server expects, parse responses correctly, and treat
    empty inputs as a fast-return without touching the network.
  - Settings.inprocess_models switches the factory between
    HTTP-client and in-process implementations.
"""
from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import paperhub.modelserver.server as server_module
from paperhub.config import load_settings
from paperhub.pipelines.embedder import (
    _HttpEmbedder,
    _SentenceTransformersEmbedder,
    get_embedder,
)
from paperhub.pipelines.embedder import reset_singleton as reset_embedder
from paperhub.rag.reranker import (
    _CrossEncoderReranker,
    _HttpReranker,
    get_reranker,
)
from paperhub.rag.reranker import reset_singleton as reset_reranker

# ─────────────────────────── server.app ────────────────────────────


@pytest.fixture
def fake_embed_model() -> Iterator[MagicMock]:
    """Inject a stub SentenceTransformer into the server module so
    no real download happens during the test."""
    fake = MagicMock()
    fake.encode.return_value = np.array(
        [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float32,
    )
    original = server_module._embed_model
    server_module._embed_model = fake
    yield fake
    server_module._embed_model = original


@pytest.fixture
def fake_rerank_model() -> Iterator[MagicMock]:
    fake = MagicMock()
    fake.predict.return_value = np.array([0.1, 0.9, 0.5], dtype=np.float32)
    original = server_module._rerank_model
    server_module._rerank_model = fake
    yield fake
    server_module._rerank_model = original


def test_health_reports_loaded_state() -> None:
    client = TestClient(server_module.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "embed_loaded" in body
    assert "rerank_loaded" in body


def test_embed_empty_short_circuits() -> None:
    """Empty texts list MUST return [] without invoking the model.
    Otherwise pre-warm calls would force a load of the real weights."""
    client = TestClient(server_module.app)
    resp = client.post("/embed", json={"texts": []})
    assert resp.status_code == 200
    assert resp.json() == {"vectors": []}


def test_embed_round_trips_vectors(fake_embed_model: MagicMock) -> None:
    client = TestClient(server_module.app)
    resp = client.post("/embed", json={"texts": ["hello", "world"]})
    assert resp.status_code == 200
    data = resp.json()
    # Float32 → JSON precision drift, so approx-compare each row.
    assert len(data["vectors"]) == 2
    assert data["vectors"][0] == pytest.approx([0.1, 0.2, 0.3], abs=1e-5)
    assert data["vectors"][1] == pytest.approx([0.4, 0.5, 0.6], abs=1e-5)
    fake_embed_model.encode.assert_called_once()


def test_rerank_empty_short_circuits() -> None:
    client = TestClient(server_module.app)
    resp = client.post(
        "/rerank", json={"query": "anything", "texts": [], "top_k": 5},
    )
    assert resp.status_code == 200
    assert resp.json() == {"indices": [], "scores": []}


def test_rerank_orders_by_score(fake_rerank_model: MagicMock) -> None:
    client = TestClient(server_module.app)
    resp = client.post(
        "/rerank",
        json={"query": "q", "texts": ["a", "b", "c"], "top_k": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Stub returned [0.1, 0.9, 0.5] → ranked indices [1, 2], scores [0.9, 0.5].
    assert data["indices"] == [1, 2]
    assert data["scores"] == pytest.approx([0.9, 0.5], abs=1e-6)


# ─────────────────────── HTTP-client wiring ────────────────────────


def test_http_embedder_serialises_request_and_parses_response() -> None:
    """The HTTP-client embedder must POST {"texts": [...]} and return
    the response vectors as an np.ndarray. Use httpx.MockTransport so
    we can assert on the actual wire payload without spinning a server."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/embed"
        body = request.read()
        import json
        payload = json.loads(body)
        assert payload == {"texts": ["x", "y"]}
        return httpx.Response(
            200, json={"vectors": [[0.1, 0.2], [0.3, 0.4]]},
        )

    emb = _HttpEmbedder("http://stub")
    emb._client = httpx.Client(transport=httpx.MockTransport(handler))

    vecs = emb.embed(["x", "y"])
    assert vecs.shape == (2, 2)
    assert vecs.dtype == np.float32
    assert np.allclose(vecs, [[0.1, 0.2], [0.3, 0.4]])


def test_http_embedder_empty_short_circuits_without_network() -> None:
    """Empty texts must NOT hit the network — otherwise pre-warm or
    rate-limited calls would burn a request budget."""

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected network call to {request.url}")

    emb = _HttpEmbedder("http://stub")
    emb._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = emb.embed([])
    assert out.shape == (0, 384)


def test_http_reranker_serialises_request_and_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rerank"
        import json
        payload = json.loads(request.read())
        assert payload == {"query": "q", "texts": ["a", "b", "c"], "top_k": 2}
        return httpx.Response(
            200, json={"indices": [1, 2], "scores": [0.9, 0.5]},
        )

    rr = _HttpReranker("http://stub")
    rr._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = rr.rerank("q", ["a", "b", "c"], top_k=2)
    assert [r.index for r in out] == [1, 2]
    assert [r.score for r in out] == pytest.approx([0.9, 0.5])


# ─────────────────────── factory dispatch ──────────────────────────


def test_factory_returns_http_client_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With inprocess_models=False (production default), get_embedder
    and get_reranker must return the HTTP-client implementations."""
    monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "0")
    reset_embedder()
    reset_reranker()
    try:
        emb = get_embedder()
        rr = get_reranker()
        assert isinstance(emb, _HttpEmbedder)
        assert isinstance(rr, _HttpReranker)
    finally:
        # Restore the in-process default that conftest set so neighbour
        # tests aren't poisoned.
        monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "1")
        reset_embedder()
        reset_reranker()


def test_factory_returns_inprocess_when_settings_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "1")
    reset_embedder()
    reset_reranker()
    emb = get_embedder()
    rr = get_reranker()
    assert isinstance(emb, _SentenceTransformersEmbedder)
    assert isinstance(rr, _CrossEncoderReranker)


def test_settings_inprocess_flag_parses_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PAPERHUB_INPROCESS_MODELS`` accepts 1/true/anything-non-zero
    as truthy, and 0/empty/false as falsy."""
    for truthy in ("1", "true", "True", "yes"):
        monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", truthy)
        assert load_settings().inprocess_models is True
    for falsy in ("0", "", "false", "False"):
        monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", falsy)
        assert load_settings().inprocess_models is False
