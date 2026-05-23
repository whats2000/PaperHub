# tests/test_marker_health.py
"""Tests for marker_available() health probe (F2.1 Task 3)."""
from __future__ import annotations

import httpx

from paperhub.pipelines.marker_health import marker_available


def _make_transport(status_code: int, body: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def _make_error_transport(exc: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


class TestMarkerAvailable:
    def test_200_models_loaded_true_returns_true(self) -> None:
        transport = _make_transport(200, {"status": "ok", "models_loaded": True, "use_llm": False})
        result = marker_available(base_url="http://marker:8002", transport=transport)
        assert result is True

    def test_200_models_loaded_false_still_returns_true(self) -> None:
        """Warming up (models_loaded=false) is still reachable — return True."""
        transport = _make_transport(200, {"status": "ok", "models_loaded": False, "use_llm": False})
        result = marker_available(base_url="http://marker:8002", transport=transport)
        assert result is True

    def test_connect_error_returns_false(self) -> None:
        transport = _make_error_transport(httpx.ConnectError("down"))
        result = marker_available(base_url="http://marker:8002", transport=transport)
        assert result is False

    def test_503_returns_false(self) -> None:
        transport = _make_transport(503, {"detail": "service unavailable"})
        result = marker_available(base_url="http://marker:8002", transport=transport)
        assert result is False
