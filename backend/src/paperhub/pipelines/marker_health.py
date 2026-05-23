# backend/src/paperhub/pipelines/marker_health.py
"""Lightweight reachability probe for the Marker PDF-extraction service (F2.1)."""
from __future__ import annotations

import httpx

from paperhub.config import load_settings


def marker_available(
    *,
    base_url: str | None = None,
    timeout: float = 2.0,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """Return ``True`` iff the Marker service answers ``GET /health`` with HTTP 200.

    Only reachability matters — ``models_loaded`` may be ``false`` while the
    service is warming up, and that still counts as available.  Any exception
    (connect error, timeout, non-200 status) returns ``False`` without raising.

    Parameters
    ----------
    base_url:
        Override the configured ``marker_service_url``.  Defaults to
        ``load_settings().marker_service_url``.
    timeout:
        Total request timeout in seconds.  Default 2 s (cheap probe).
    transport:
        Injectable ``httpx.BaseTransport`` for testing (``httpx.MockTransport``).
    """
    effective_url = (base_url or load_settings().marker_service_url).rstrip("/")
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            resp = client.get(f"{effective_url}/health")
        return resp.status_code == 200
    except Exception:
        return False
