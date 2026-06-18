"""GET /version — running version + optional GitHub update check (FR-16).

PaperHub is self-hosted; this endpoint only informs (it never self-updates).
The GitHub lookup is gated by PAPERHUB_UPDATE_CHECK, short-timeout, TTL-cached,
and failure-swallowing — a network problem never breaks the endpoint.
"""
from __future__ import annotations

import os
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

import httpx
from fastapi import APIRouter

router = APIRouter()

_DEFAULT_REPO = "whats2000/PaperHub"
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6h

# Module-level cache: (latest, html_url, checked_at_iso, monotonic_expiry).
_cache: tuple[str | None, str | None, str | None, float] | None = None


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None


def _current_version() -> str:
    try:
        return pkg_version("paperhub")
    except PackageNotFoundError:  # pragma: no cover - dev editable edge
        return "0.0.0"


def _parse_semver(v: str) -> tuple[int, int, int]:
    parts = v.lstrip("v").split(".")[:3]
    nums = []
    for p in parts:
        digits = "".join(ch for ch in p if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _is_newer(latest: str, current: str) -> bool:
    return _parse_semver(latest) > _parse_semver(current)


async def _fetch_latest_release(repo: str) -> tuple[str | None, str | None]:
    """Return (latest_version_without_v, html_url) from GitHub, or (None, None).
    Monkeypatched in tests; never raises to the caller in practice."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    tag = str(data.get("tag_name", "")).lstrip("v")
    html_url = data.get("html_url")
    return (tag or None, html_url)


@router.get("/version")
async def get_version() -> dict[str, object]:
    global _cache
    current = _current_version()
    enabled = os.environ.get("PAPERHUB_UPDATE_CHECK", "1") != "0"
    if not enabled:
        return {
            "current": current,
            "latest": None,
            "update_available": False,
            "html_url": None,
            "checked_at": None,
        }

    now = time.monotonic()
    if _cache is None or now >= _cache[3]:
        latest: str | None = None
        html_url: str | None = None
        try:
            repo = os.environ.get("PAPERHUB_GITHUB_REPO", _DEFAULT_REPO)
            latest, html_url = await _fetch_latest_release(repo)
        except Exception:
            latest, html_url = None, None  # swallow: never break the endpoint
        checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _cache = (latest, html_url, checked_at, now + _CACHE_TTL_SECONDS)

    assert _cache is not None
    latest_out: str | None
    html_url_out: str | None
    checked_at_out: str | None
    latest_out, html_url_out, checked_at_out, _ = _cache
    return {
        "current": current,
        "latest": latest_out,
        "update_available": bool(latest_out and _is_newer(latest_out, current)),
        "html_url": html_url_out,
        "checked_at": checked_at_out,
    }
