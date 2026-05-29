"""Tests for Unpaywall OA-PDF lookup client (SRS F4.3).

Contract under test: ``paperhub.pipelines.unpaywall.find_oa_pdf_by_doi``
  - Signature: ``async (doi: str, *, email: str) -> str | None``
  - NEVER raises — every error path returns None.
  - ``email`` query param is always present on the request.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from paperhub.pipelines.unpaywall import (
    UNPAYWALL_BASE,
    find_oa_pdf_by_doi,
)

pytestmark = pytest.mark.asyncio

_DOI = "10.1038/s41586-025-08567-9"
_EMAIL = "ops@example.com"
_PDF_URL = "https://www.nature.com/articles/s41586-025-08567-9.pdf"
_LANDING_URL = "https://www.nature.com/articles/s41586-025-08567-9"

_ENDPOINT = f"{UNPAYWALL_BASE}/{_DOI}"


# ---------------------------------------------------------------------------
# Happy paths (Step 1)
# ---------------------------------------------------------------------------


@respx.mock
async def test_happy_path_url_for_pdf_returned() -> None:
    """200 + is_oa=true + best_oa_location.url_for_pdf → that URL exactly."""
    route = respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "is_oa": True,
                "best_oa_location": {
                    "url_for_pdf": _PDF_URL,
                    "url": _LANDING_URL,
                },
            },
        ),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert route.called
    assert result == _PDF_URL


@respx.mock
async def test_happy_path_fallback_to_url_when_no_pdf() -> None:
    """200 + is_oa=true + url_for_pdf=None + url set → landing-page url."""
    route = respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "is_oa": True,
                "best_oa_location": {
                    "url_for_pdf": None,
                    "url": _LANDING_URL,
                },
            },
        ),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert route.called
    assert result == _LANDING_URL


# ---------------------------------------------------------------------------
# Miss paths → None (Step 2)
# ---------------------------------------------------------------------------


@respx.mock
async def test_returns_none_when_both_pdf_and_landing_url_missing() -> None:
    """is_oa=true with best_oa_location set but BOTH url_for_pdf and url
    are None — a well-formed but unusable Unpaywall response. Must
    return None, not '' or False, so the dispatcher falls through to
    NoIngestibleSourceError cleanly."""
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "is_oa": True,
                "best_oa_location": {"url_for_pdf": None, "url": None},
            },
        ),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert result is None


@respx.mock
async def test_is_oa_false_returns_none() -> None:
    """is_oa=false → None regardless of other fields."""
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "is_oa": False,
                "best_oa_location": {
                    "url_for_pdf": _PDF_URL,
                    "url": _LANDING_URL,
                },
            },
        ),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert result is None


@respx.mock
async def test_no_best_oa_location_returns_none() -> None:
    """is_oa=true but best_oa_location=null → None."""
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "is_oa": True,
                "best_oa_location": None,
            },
        ),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert result is None


@respx.mock
async def test_404_returns_none() -> None:
    """404 (DOI not indexed by Unpaywall) → None, does not raise."""
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(404, json={"message": "not found"}),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert result is None


@respx.mock
async def test_429_returns_none() -> None:
    """429 (rate-limited) → None, does not raise."""
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(429, json={"message": "rate limited"}),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert result is None


@respx.mock
async def test_5xx_returns_none() -> None:
    """5xx (Unpaywall outage) → None, does not raise."""
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(503, json={"message": "service unavailable"}),
    )
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert result is None


@respx.mock
async def test_transport_error_returns_none() -> None:
    """httpx.ConnectError (network down) → None, does not raise."""
    respx.get(_ENDPOINT).mock(side_effect=httpx.ConnectError("connection refused"))
    result = await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert result is None


# ---------------------------------------------------------------------------
# Email query param present on every request (Step 3)
# ---------------------------------------------------------------------------


@respx.mock
async def test_email_query_param_is_sent() -> None:
    """The ``email`` query param must be present on every Unpaywall request."""
    route = respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "is_oa": True,
                "best_oa_location": {
                    "url_for_pdf": _PDF_URL,
                    "url": _LANDING_URL,
                },
            },
        ),
    )
    await find_oa_pdf_by_doi(_DOI, email=_EMAIL)
    assert route.called
    sent_url = httpx.URL(str(route.calls[0].request.url))
    assert sent_url.params["email"] == _EMAIL
