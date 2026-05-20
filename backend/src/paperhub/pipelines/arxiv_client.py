"""arXiv API client: search + e-print source download.

Adapted from paper2slides-plus/src/arxiv_utils.py — extraction + download
patterns copied + edited to fit the Plan-C Paper Pipeline contract.
"""
from __future__ import annotations

import logging
import random
import re
import tarfile
import time
from datetime import UTC
from pathlib import Path

import arxiv
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_client = arxiv.Client()

# Tarballs can be 30+ MB and export.arxiv.org sometimes throttles to a few
# hundred KB/s. 120 s total read budget covers ~50 MB at 400 KB/s with margin;
# connect stays tight so a hung DNS / firewall fails fast.
_DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
# arXiv asks for a contactable User-Agent per their Terms of Use.
# https://info.arxiv.org/help/api/tou.html
_USER_AGENT = "PaperHub/0.1 (https://github.com/whats2000/PaperHub)"

# arxiv's export mirror occasionally drops large transfers mid-stream
# (httpx.RemoteProtocolError "peer closed connection without sending
# complete message body"). Retry with backoff before failing the ingest.
# After a drop arxiv often returns 429 on subsequent attempts (per-IP
# rate limit kicks in); the resume path observed this empirically on
# arxiv:2605.02881 (~41MB tarball dropped at exactly 8MB, then 429).
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_BACKOFF_BASE_S = 2.0
# Fallback wait when arxiv sends 429 without Retry-After. Kept short
# (5s) because arxiv's actual per-IP cooldown is brief; long waits
# don't help when the underlying issue is a per-connection byte cap
# rather than a rate-limit window.
_RATE_LIMIT_DEFAULT_BACKOFF_S = 5.0
# HTTP status codes worth retrying. 429 = rate-limited, 5xx = server
# trouble. Other 4xx (404, 403, 416-as-non-complete) stay non-retryable.
_RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_TRANSIENT_DOWNLOAD_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse the ``Retry-After`` header. Accepts an integer seconds value
    OR an HTTP-date. Returns the wait in seconds, or None if absent /
    unparseable / in the past."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        target = parsedate_to_datetime(value)
        from datetime import datetime
        now = datetime.now(UTC)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        delta = (target - now).total_seconds()
        return delta if delta > 0 else None
    except (TypeError, ValueError):
        return None


class TarballCorrupt(RuntimeError):
    """Raised when arxiv's e-print tarball downloaded successfully (HTTP
    OK, full byte count) but is structurally unreadable as a gzip+tar
    archive. The Paper Pipeline catches this and falls back to PDF
    ingest — equation fidelity is lower but the paper is still
    ingestible end-to-end.
    """


class ArxivResult(BaseModel):
    arxiv_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    pdf_url: str | None = None


_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def _id_from_entry_id(entry_id: str) -> str:
    """Strip URL prefix + version suffix: 'http://arxiv.org/abs/2403.01234v2' → '2403.01234'."""
    m = _ARXIV_ID_RE.search(entry_id)
    if not m:
        raise ValueError(f"unexpected arxiv entry_id: {entry_id!r}")
    return m.group(1)


def search_arxiv(query: str, max_results: int = 10) -> list[ArxivResult]:
    """Return metadata-only search results from arXiv. No download."""
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
    )
    results: list[ArxivResult] = []
    for r in _client.results(search):
        results.append(
            ArxivResult(
                arxiv_id=_id_from_entry_id(r.entry_id),
                title=r.title.strip(),
                authors=[a.name for a in r.authors],
                year=getattr(r.published, "year", None),
                abstract=r.summary.strip(),
                pdf_url=r.pdf_url if isinstance(getattr(r, "pdf_url", None), str) else None,
            )
        )
    return results


def fetch_arxiv_by_id(arxiv_id: str) -> ArxivResult | None:
    """Exact-ID metadata lookup — returns the paper for ``arxiv_id`` or None.

    Unlike :func:`search_arxiv` (a relevance query that returns the
    nearest-matching paper for ANY string), this uses arXiv's ``id_list``
    so a non-existent / hallucinated id returns ``None`` instead of a
    confidently-wrong neighbour. Used to VERIFY an LLM-claimed arxiv_id
    before trusting it: if it resolves the paper exists and we adopt
    arXiv's authoritative title; if not, the id is bogus and the caller
    drops it. Best-effort: any arXiv API error is logged and treated as
    "unverifiable" (``None``) rather than propagated.
    """
    try:
        search = arxiv.Search(id_list=[arxiv_id])
        for r in _client.results(search):
            return ArxivResult(
                arxiv_id=_id_from_entry_id(r.entry_id),
                title=r.title.strip(),
                authors=[a.name for a in r.authors],
                year=getattr(r.published, "year", None),
                abstract=r.summary.strip(),
                pdf_url=r.pdf_url if isinstance(getattr(r, "pdf_url", None), str) else None,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort verification
        logger.warning(
            "fetch_arxiv_by_id(%r) failed (%s: %s); treating as unverifiable",
            arxiv_id, type(exc).__name__, exc,
        )
    return None


def _download_with_resume(url: str, target_path: Path) -> None:
    """Download ``url`` to ``target_path`` with byte-range resume +
    fast retry for transient failures, but FAIL FAST on the file-size
    pattern (partial bytes received then connection dropped).

    Rationale: arxiv's export mirror caps per-connection delivery for
    large papers (observed: ~8 MB for /src/, ~1 MB for /pdf/ on
    2605.02881 which is 41 MB / 27 MB total). When this happens, no
    amount of resume / retry from the same byte offset will recover —
    arxiv either returns 429 to the resume request or drops again at
    the same boundary. The CALLER should fall back to a different
    method (e.g. PDF instead of source tarball) rather than spin here.

    Decision matrix on each attempt:

      * 2xx success → return.
      * 416 + existing bytes → file already complete, return.
      * RemoteProtocolError WITH bytes received this attempt → SIZE
        CAP HIT; raise immediately so the caller skips to the
        fallback method. NO retry — every retry would hit the same
        per-connection byte limit at the same offset.
      * Everything else retriable (connect/read errors with zero
        bytes received, 429, 5xx) → up to ``_DOWNLOAD_MAX_ATTEMPTS``
        attempts with short backoff (honouring Retry-After when
        present).
      * 200 with existing partial bytes → server ignored Range; wipe
        + restart from byte 0.

    Partial bytes are kept on disk on every branch so the caller can
    inspect what was received.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        existing_bytes = target_path.stat().st_size if target_path.exists() else 0
        headers: dict[str, str] = {"User-Agent": _USER_AGENT}
        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"
        bytes_before_attempt = existing_bytes

        try:
            with httpx.stream(
                "GET", url,
                timeout=_DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                headers=headers,
            ) as resp:
                if resp.status_code == 416 and existing_bytes > 0:
                    logger.info(
                        "download_with_resume %s: 416 Range Not Satisfiable "
                        "with existing=%d bytes; treating as complete",
                        url, existing_bytes,
                    )
                    return
                if resp.status_code in _RETRYABLE_HTTP_STATUS:
                    # All 429 / 5xx get the standard 3-attempt retry
                    # with short backoff — this is the "other pattern"
                    # bucket. Only the bytes-then-drop transport
                    # failure (caught below) is treated as a size cap
                    # and skipped to fallback.
                    if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                        logger.warning(
                            "download_with_resume %s: HTTP %d after %d "
                            "attempts; giving up",
                            url, resp.status_code, attempt,
                        )
                        resp.raise_for_status()
                    retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                    backoff = (
                        retry_after
                        if retry_after is not None
                        else _RATE_LIMIT_DEFAULT_BACKOFF_S
                    )
                    backoff += random.uniform(0, 0.5)
                    logger.warning(
                        "download_with_resume %s: HTTP %d attempt %d/%d "
                        "(no partial bytes); sleeping %.1fs",
                        url, resp.status_code, attempt,
                        _DOWNLOAD_MAX_ATTEMPTS, backoff,
                    )
                    time.sleep(backoff)
                    continue
                if existing_bytes > 0 and resp.status_code == 200:
                    logger.info(
                        "download_with_resume %s: server returned 200 "
                        "despite Range header; restarting from byte 0",
                        url,
                    )
                    target_path.unlink(missing_ok=True)
                    existing_bytes = 0
                resp.raise_for_status()
                mode = "ab" if existing_bytes > 0 else "wb"
                with target_path.open(mode) as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
            return  # success
        except _TRANSIENT_DOWNLOAD_EXCEPTIONS as exc:
            last_exc = exc
            new_bytes = target_path.stat().st_size if target_path.exists() else 0
            bytes_this_attempt = new_bytes - bytes_before_attempt
            # SIZE-CAP signature: we successfully received bytes this
            # attempt before the connection dropped. Retrying from the
            # same offset will hit the same wall — skip to fallback.
            if bytes_this_attempt > 0:
                logger.warning(
                    "download_with_resume %s: connection dropped mid-"
                    "stream after %d bytes this attempt (total=%d, %s); "
                    "size-cap hit, skipping to fallback",
                    url, bytes_this_attempt, new_bytes, type(exc).__name__,
                )
                raise
            # Transient connection-only error (no bytes received).
            # Fast retry.
            if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                logger.warning(
                    "download_with_resume %s: failed after %d attempts "
                    "(%s: %s); final partial=%d bytes",
                    url, attempt, type(exc).__name__, exc, new_bytes,
                )
                raise
            backoff = _DOWNLOAD_BACKOFF_BASE_S * (2 ** (attempt - 1))
            backoff += random.uniform(0, 0.5)
            logger.warning(
                "download_with_resume %s: attempt %d/%d transient "
                "(%s: %s); fast retry in %.1fs",
                url, attempt, _DOWNLOAD_MAX_ATTEMPTS,
                type(exc).__name__, exc, backoff,
            )
            time.sleep(backoff)
    # pragma: no cover — loop exits via return or raise.
    if last_exc is not None:
        raise last_exc


def download_arxiv_pdf(arxiv_id: str, *, cache_root: Path) -> Path:
    """Download the rendered PDF for ``arxiv_id`` to
    ``cache_root / arxiv_id / source.pdf`` and return the path.

    Used as the fallback when ``download_arxiv_source`` exhausts its
    retry budget on the e-print tarball — every arxiv paper publishes
    a PDF even when the LaTeX source is missing or refuses to download.
    Equation fidelity is lower than LaTeX rendering but the paper is
    still ingestible end-to-end.

    Resume-capable via the same byte-range path as
    ``download_arxiv_source``.
    """
    target_dir = cache_root / arxiv_id
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = target_dir / "source.pdf"
    # Same export → main mirror promotion as download_arxiv_source.
    # The export mirror caps PDF transfers too (observed: 1-2 MB drop
    # on the 27 MB MolmoACT2 PDF); the main site doesn't.
    try:
        _download_with_resume(
            f"https://export.arxiv.org/pdf/{arxiv_id}", pdf_path,
        )
    except httpx.RemoteProtocolError:
        new_bytes = pdf_path.stat().st_size if pdf_path.exists() else 0
        if new_bytes <= 0:
            raise
        logger.warning(
            "arxiv PDF export-mirror size-cap hit for %s (%d bytes "
            "received); retrying via main arxiv.org/pdf/",
            arxiv_id, new_bytes,
        )
        pdf_path.unlink(missing_ok=True)
        _download_with_resume(
            f"https://arxiv.org/pdf/{arxiv_id}", pdf_path,
        )
    return pdf_path


def download_arxiv_source(arxiv_id: str, *, cache_root: Path) -> Path:
    """Download the e-print source tarball for an arxiv_id, unpack into
    cache_root / arxiv_id / source/ — preserving the tarball's directory
    structure so ``\\input{sections/foo}`` directives resolve.  Returns the
    source directory.
    """
    target_dir = cache_root / arxiv_id
    source_dir = target_dir / "source"
    target_dir.mkdir(parents=True, exist_ok=True)

    # arxiv hosts the e-print at TWO mirrors that we try in order:
    #
    #   1. https://export.arxiv.org/src/<id>   (preferred — programmatic
    #      policy mirror, https://info.arxiv.org/help/robots.html). For
    #      most papers (sub-30 MB) this is fast + reliable.
    #   2. https://arxiv.org/src/<id>          (fallback for big papers).
    #      The export mirror caps per-connection delivery and drops
    #      large transfers mid-stream (observed empirically: 8 MB cap
    #      on arxiv:2605.02881 which is 41 MB). The main site doesn't
    #      have the same cap; live testing confirmed it serves the
    #      full 41 MB without dropping.
    #
    # We promote to mirror #2 ONLY on the size-cap signature
    # (RemoteProtocolError with bytes received). Other transient errors
    # stay on the export mirror — they're not size-related and
    # bouncing to the main site doesn't help, just adds load there.
    tar_path = target_dir / f"{arxiv_id}.tar.gz"
    try:
        _download_with_resume(
            f"https://export.arxiv.org/src/{arxiv_id}", tar_path,
        )
    except httpx.RemoteProtocolError:
        new_bytes = tar_path.stat().st_size if tar_path.exists() else 0
        if new_bytes <= 0:
            # No bytes received → not a size-cap; transport failure.
            # Don't bounce to the main site; raise to caller for PDF
            # fallback.
            raise
        logger.warning(
            "arxiv source export-mirror size-cap hit for %s (%d bytes "
            "received); retrying via main arxiv.org/src/ which does "
            "not impose the same per-connection cap",
            arxiv_id, new_bytes,
        )
        # Wipe the partial bytes — different server, no point sending
        # a Range header pointing at export.arxiv.org's offset.
        tar_path.unlink(missing_ok=True)
        _download_with_resume(
            f"https://arxiv.org/src/{arxiv_id}", tar_path,
        )

    source_dir.mkdir(parents=True, exist_ok=True)
    # Resolve once so we can sanity-check that every extracted member stays
    # inside source_dir even after symlink/`..` resolution.
    source_dir_resolved = source_dir.resolve()
    try:
        # If the tarball turned out corrupt (e.g. all retries together
        # still left a truncated gzip stream), surface a TarballCorrupt
        # so the caller can fall back to PDF rather than aborting ingest.
        try:
            tar = tarfile.open(tar_path, "r:gz")  # noqa: SIM115
        except (tarfile.ReadError, EOFError, OSError) as exc:
            tar_path.unlink(missing_ok=True)
            raise TarballCorrupt(
                f"arxiv source tarball for {arxiv_id} is unreadable: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        with tar:
            # Preserve directory layout.  Many arxiv papers organise their
            # LaTeX with subdirectories (sections/, figures/, etc.); flattening
            # would break `\input{sections/foo}` resolution silently.  Refuse
            # any member whose path would escape source_dir.
            for member in tar.getmembers():
                if not member.isreg():
                    continue
                rel = Path(member.name)
                if rel.is_absolute() or any(part == ".." for part in rel.parts):
                    continue  # path-traversal — skip silently
                target_path = source_dir / rel
                # Re-check after resolve() in case of symlink shenanigans.
                if not str(target_path.resolve()).startswith(
                    str(source_dir_resolved),
                ):
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                fobj = tar.extractfile(member)
                if fobj is None:
                    continue
                target_path.write_bytes(fobj.read())
    finally:
        tar_path.unlink(missing_ok=True)
    return source_dir
