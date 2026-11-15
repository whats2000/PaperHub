import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import arxiv
import httpx
import pytest
import respx

from paperhub.pipelines.arxiv_client import (
    TarballCorrupt,
    download_arxiv_pdf,
    download_arxiv_source,
)


def test_arxiv_module_has_expected_api_shape() -> None:
    """Contract test against the installed arxiv module — fails fast on
    API drift across major version bumps."""
    assert hasattr(arxiv, "Client")
    assert hasattr(arxiv, "Search")
    assert hasattr(arxiv, "Result")
    client = arxiv.Client()
    assert callable(getattr(client, "results", None)), (
        "arxiv.Client.results missing — fetch_arxiv_by_id needs rewrite"
    )
    # Result.source_url() exists in the installed arxiv library.
    # download_arxiv_source no longer DEPENDS on it (we build the URL
    # directly), but we still assert its presence to catch major API
    # drift early.
    assert callable(getattr(arxiv.Result, "source_url", None)), (
        "arxiv.Result.source_url missing — if re-adding metadata query, needs rewrite"
    )


def test_no_free_text_arxiv_search() -> None:
    """Final review: the free-text arXiv search (no outbound checkpoint, on
    no palette) is gone; arXiv is reached by exact id only."""
    from paperhub.agents import research_tools
    from paperhub.pipelines import arxiv_client

    assert not hasattr(arxiv_client, "search_arxiv")
    assert not hasattr(research_tools, "search_arxiv_dispatch")


@respx.mock
def test_download_arxiv_source_writes_to_cache(tmp_path: Path) -> None:
    """download_arxiv_source fetches the tarball via httpx and unpacks it.

    The arxiv metadata API (_client.results) must NOT be called — the source
    URL is built deterministically from the arxiv_id (fix for 429 bug).
    """
    # Build an in-memory tarball with a single main.tex.
    src_text = r"\documentclass{article}\begin{document}Hi\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(src_text)
        tar.addfile(info, io.BytesIO(src_text.encode("utf-8")))
    tarball_bytes = buf.getvalue()

    # Mock only the HTTP GET for the source URL — no arxiv API mock needed.
    respx.get("https://export.arxiv.org/src/2403.01234").mock(
        return_value=httpx.Response(200, content=tarball_bytes),
    )

    # Verify _client.results is NOT called during download.
    with patch(
        "paperhub.pipelines.arxiv_client._client.results",
        side_effect=AssertionError("_client.results must not be called by download_arxiv_source"),
    ):
        source_dir = download_arxiv_source("2403.01234", cache_root=tmp_path / "cache")

    assert source_dir.exists()
    assert (source_dir / "main.tex").exists()
    assert source_dir.parent.name == "2403.01234"


@respx.mock
def test_download_arxiv_source_builds_src_url_without_arxiv_metadata_query(
    tmp_path: Path,
) -> None:
    """download_arxiv_source must build the source URL from the arxiv_id
    directly, without calling _client.results at all."""
    src_text = r"\documentclass{article}\begin{document}Test\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="paper.tex")
        info.size = len(src_text)
        tar.addfile(info, io.BytesIO(src_text.encode("utf-8")))
    tarball_bytes = buf.getvalue()

    respx.get("https://export.arxiv.org/src/1234.56789").mock(
        return_value=httpx.Response(200, content=tarball_bytes),
    )

    mock_results = MagicMock(
        side_effect=AssertionError("_client.results called — no metadata query allowed"),
    )
    with patch("paperhub.pipelines.arxiv_client._client.results", mock_results):
        source_dir = download_arxiv_source("1234.56789", cache_root=tmp_path / "cache")

    mock_results.assert_not_called()
    assert (source_dir / "paper.tex").exists()


@respx.mock
def test_download_arxiv_source_preserves_subdirs(tmp_path: Path) -> None:
    """download_arxiv_source must preserve the tarball's directory structure.

    Flattening to a single dir would break `\\input{sections/method}` resolution
    in LaTeX — and silently, since extract.py emits no error on missing
    inputs.  Confirm `sections/method.tex` lands inside `source/sections/`,
    not `source/`.
    """
    main_text = (
        r"\documentclass{article}\begin{document}"
        r"\input{sections/method}"
        r"\end{document}"
    )
    method_text = r"This is the method section content."
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in (("main.tex", main_text), ("sections/method.tex", method_text)):
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body.encode("utf-8")))
    tarball = buf.getvalue()

    respx.get("https://export.arxiv.org/src/2510.03293").mock(
        return_value=httpx.Response(200, content=tarball),
    )

    source_dir = download_arxiv_source("2510.03293", cache_root=tmp_path / "cache")

    assert (source_dir / "main.tex").exists()
    # Subdir must survive; flattening would have placed method.tex at the root.
    assert (source_dir / "sections" / "method.tex").exists()
    assert not (source_dir / "method.tex").exists(), (
        "method.tex must NOT have been flattened to root — would break "
        "\\input{sections/method} resolution"
    )
    assert (source_dir / "sections" / "method.tex").read_text(encoding="utf-8") == method_text


@respx.mock
def test_download_arxiv_source_rejects_path_traversal(tmp_path: Path) -> None:
    """Tarball members with `..` or absolute paths must be silently dropped
    so a malicious or malformed tarball can't write outside source/."""
    main_text = r"\documentclass{article}\begin{document}safe\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in (
            ("main.tex", main_text),
            ("../escape.tex", r"should not land outside source/"),
            ("safe/inner.tex", r"should land inside source/safe/"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body.encode("utf-8")))
    tarball = buf.getvalue()

    respx.get("https://export.arxiv.org/src/0001.00001").mock(
        return_value=httpx.Response(200, content=tarball),
    )

    source_dir = download_arxiv_source("0001.00001", cache_root=tmp_path / "cache")

    assert (source_dir / "main.tex").exists()
    assert (source_dir / "safe" / "inner.tex").exists()
    # The `..`/escape path must not have escaped source_dir.
    assert not (source_dir.parent / "escape.tex").exists()
    assert not (source_dir / "escape.tex").exists()


def test_download_arxiv_source_retries_on_transient_transport_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """export.arxiv.org occasionally drops large transfers mid-stream
    (httpx.RemoteProtocolError "peer closed connection without sending
    complete message body"). The download must retry transient transport
    errors before failing the ingest."""
    # Build a real tarball that the retry path will eventually receive.
    src_text = r"\documentclass{article}\begin{document}Retried\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(src_text)
        tar.addfile(info, io.BytesIO(src_text.encode("utf-8")))
    tarball_bytes = buf.getvalue()

    # First call: raise RemoteProtocolError (simulating arxiv's drop).
    # Second call: return the real tarball.
    call_count = {"n": 0}

    class _FakeStream:
        def __init__(self, fail_first: bool) -> None:
            self._fail_first = fail_first
            # The resume-capable downloader reads status_code to decide
            # whether to append (206) / restart (200) / treat-as-done
            # (416). Stub the second-attempt response as 206 so the
            # resume path is exercised.
            self.status_code = 206 if not fail_first else 200

        def __enter__(self) -> "_FakeStream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            if self._fail_first:
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body",
                )
            return iter([tarball_bytes])

    def _fake_httpx_stream(
        *_args: object, **kwargs: object,
    ) -> _FakeStream:
        call_count["n"] += 1
        # The resume path passes Range: bytes=<N>- on attempt #2.
        # When no Range header is present (attempt #1) we MUST claim
        # 200 OK; when Range IS present (attempt #2) we MUST claim 206.
        headers = kwargs.get("headers") or {}
        has_range = isinstance(headers, dict) and "Range" in headers
        stream = _FakeStream(fail_first=call_count["n"] == 1)
        stream.status_code = 206 if has_range else 200
        return stream

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream", _fake_httpx_stream,
    )
    # Avoid the real exponential backoff sleep so the test stays fast.
    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.time.sleep", lambda _s: None,
    )

    source_dir = download_arxiv_source("2503.00001", cache_root=tmp_path / "cache")
    assert (source_dir / "main.tex").exists()
    assert call_count["n"] == 2  # one failure, one success


def test_download_arxiv_source_gives_up_after_max_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After exhausting the retry budget the transport error propagates so
    the API layer can return a meaningful 5xx + the user can see a Retry
    button instead of an opaque 500."""
    call_count = {"n": 0}

    class _AlwaysFailStream:
        # Default to 200 OK so the resume-path check doesn't trip on
        # the missing-attribute branch; iter_bytes raises before status
        # actually matters anyway.
        status_code = 200

        def __enter__(self) -> "_AlwaysFailStream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            raise httpx.RemoteProtocolError("transient failure")

    def _fake_httpx_stream(*_args: object, **_kwargs: object) -> _AlwaysFailStream:
        call_count["n"] += 1
        return _AlwaysFailStream()

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream", _fake_httpx_stream,
    )
    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.time.sleep", lambda _s: None,
    )

    with pytest.raises(httpx.RemoteProtocolError):
        download_arxiv_source("2503.00002", cache_root=tmp_path / "cache")
    # _AlwaysFailStream raises before any bytes are written, so each
    # attempt counts as "transient with 0 bytes received" → fast retry
    # up to _DOWNLOAD_MAX_ATTEMPTS (3).
    assert call_count["n"] == 3


def test_download_arxiv_source_retries_on_429_with_retry_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: arxiv often replies HTTP 429 after a mid-stream drop
    (per-IP rate limiter kicks in once the original transfer is
    cancelled). The downloader must NOT treat 429 as terminal — it
    must honour ``Retry-After`` and retry. Without this, the first
    drop poisons the rest of the ingest path because both the source
    retries AND the PDF fallback hit 429."""
    src_text = r"\documentclass{article}\begin{document}429ok\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(src_text)
        tar.addfile(info, io.BytesIO(src_text.encode("utf-8")))
    tarball_bytes = buf.getvalue()

    seen_sleeps: list[float] = []
    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.time.sleep",
        lambda s: seen_sleeps.append(s),
    )

    class _Stream:
        def __init__(self, status: int, body: bytes,
                     retry_after: str | None = None) -> None:
            self.status_code = status
            self._body = body
            self.headers = {"retry-after": retry_after} if retry_after else {}

        def __enter__(self) -> "_Stream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            if 400 <= self.status_code < 600:
                raise httpx.HTTPStatusError(
                    f"{self.status_code}", request=None, response=None,
                )

        def iter_bytes(self) -> object:
            return iter([self._body])

    call_count = {"n": 0}

    def _fake_stream(*_args: object, **_kwargs: object) -> _Stream:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 429 with Retry-After: 3 seconds.
            return _Stream(429, b"", retry_after="3")
        return _Stream(200, tarball_bytes)

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream", _fake_stream,
    )

    source_dir = download_arxiv_source(
        "2503.00098", cache_root=tmp_path / "cache",
    )
    assert call_count["n"] == 2
    # The retry-after value (3s) was used as the sleep (plus jitter
    # under 0.5s).
    assert seen_sleeps, "expected at least one sleep call"
    assert 3.0 <= seen_sleeps[0] <= 3.5, (
        f"expected retry-after=3 honoured, got sleep={seen_sleeps[0]!r}"
    )
    assert (source_dir / "main.tex").exists()


def test_download_arxiv_source_skips_on_partial_drop_for_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: when the FIRST attempt receives some bytes then the
    connection drops mid-stream, that's the arxiv per-connection
    size-cap pattern. The downloader must FAIL FAST (no retry from
    the same byte offset) so the caller can skip to the PDF
    fallback. Retrying from the same offset would just hit the same
    cap again — observed empirically on arxiv:2605.02881."""
    src_text = r"\documentclass{article}\begin{document}Big paper\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(src_text)
        tar.addfile(info, io.BytesIO(src_text.encode("utf-8")))
    tarball_bytes = buf.getvalue()
    split = max(1, len(tarball_bytes) // 3)
    first_chunk = tarball_bytes[:split]

    call_count = {"n": 0}

    class _Stream:
        status_code = 200

        def __enter__(self) -> "_Stream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            def gen() -> object:
                yield first_chunk
                raise httpx.RemoteProtocolError("mid-stream drop")
            return gen()

    def _fake_stream(*_args: object, **_kwargs: object) -> _Stream:
        call_count["n"] += 1
        return _Stream()

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream", _fake_stream,
    )
    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.time.sleep", lambda _s: None,
    )

    with pytest.raises(httpx.RemoteProtocolError):
        download_arxiv_source("2503.00099", cache_root=tmp_path / "cache")
    # Size-cap on export mirror promotes to arxiv.org/src/ (the main
    # mirror, no per-connection cap). Both mirrors hit the same stub
    # here so both fail-fast → 2 total HTTP attempts before raising
    # to the caller for PDF fallback.
    assert call_count["n"] == 2, (
        f"size-cap pattern must try export then arxiv.org main mirror "
        f"before raising; got {call_count['n']} attempts"
    )
    # Partial bytes from the LAST attempt (arxiv.org) are kept on
    # disk. The export-mirror partial was wiped during the mirror
    # promotion so the main-mirror download could start clean.
    partial = tmp_path / "cache" / "2503.00099" / "2503.00099.tar.gz"
    assert partial.exists()
    assert partial.stat().st_size == len(first_chunk)


def test_download_arxiv_source_promotes_to_main_mirror_on_export_size_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: when export.arxiv.org drops the connection
    mid-stream (its per-connection size cap on large papers), the
    downloader must retry against the main `arxiv.org/src/` mirror
    which doesn't impose the same cap. This is what lets big papers
    (40+ MB e-prints like MolmoACT2) ingest end-to-end without
    falling all the way back to PDF."""
    src_text = r"\documentclass{article}\begin{document}Big paper\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(src_text)
        tar.addfile(info, io.BytesIO(src_text.encode("utf-8")))
    tarball_bytes = buf.getvalue()
    split = max(1, len(tarball_bytes) // 3)
    first_chunk = tarball_bytes[:split]

    seen_urls: list[str] = []

    class _ExportStream:
        """First mirror: drops mid-stream like export.arxiv.org."""
        status_code = 200

        def __enter__(self) -> "_ExportStream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            def gen() -> object:
                yield first_chunk
                raise httpx.RemoteProtocolError("mid-stream drop")
            return gen()

    class _MainStream:
        """Main mirror: serves the full tarball."""
        status_code = 200

        def __enter__(self) -> "_MainStream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            return iter([tarball_bytes])

    def _fake_stream(*args: object, **_kwargs: object) -> object:
        url = args[1] if len(args) > 1 else ""
        seen_urls.append(str(url))
        if urlparse(str(url)).hostname == "export.arxiv.org":
            return _ExportStream()
        return _MainStream()

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream", _fake_stream,
    )

    source_dir = download_arxiv_source(
        "2503.00200", cache_root=tmp_path / "cache",
    )
    # Export tried first, then main mirror.
    assert len(seen_urls) == 2
    assert urlparse(seen_urls[0]).hostname == "export.arxiv.org"
    assert seen_urls[1] == "https://arxiv.org/src/2503.00200"
    assert urlparse(seen_urls[1]).hostname != "export.arxiv.org"
    # Tarball reconstructed + extracted from the main-mirror response.
    assert (source_dir / "main.tex").exists()
    assert (source_dir / "main.tex").read_text(encoding="utf-8") == src_text


def test_download_arxiv_source_resumes_across_separate_invocations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even though the in-loop retry path is gone, the resume
    mechanism is still meaningful across SEPARATE invocations: if a
    partial file is already on disk (e.g. from a previous backend
    run that died mid-download), the next call must issue
    ``Range: bytes=<existing>-`` so bytes already on disk aren't
    re-downloaded."""
    src_text = r"\documentclass{article}\begin{document}Resumed\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(src_text)
        tar.addfile(info, io.BytesIO(src_text.encode("utf-8")))
    tarball_bytes = buf.getvalue()
    split = max(1, len(tarball_bytes) // 3)

    # Pre-seed the cache with a partial download from a "previous run".
    cache_root = tmp_path / "cache"
    target_dir = cache_root / "2503.00099"
    target_dir.mkdir(parents=True)
    (target_dir / "2503.00099.tar.gz").write_bytes(tarball_bytes[:split])

    seen_ranges: list[str | None] = []

    class _Stream:
        status_code = 206

        def __enter__(self) -> "_Stream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            return iter([tarball_bytes[split:]])

    def _fake_stream(*_args: object, **kwargs: object) -> _Stream:
        headers = kwargs.get("headers") or {}
        rng = headers.get("Range") if isinstance(headers, dict) else None
        seen_ranges.append(rng)
        return _Stream()

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream", _fake_stream,
    )

    source_dir = download_arxiv_source("2503.00099", cache_root=cache_root)
    assert seen_ranges == [f"bytes={split}-"], (
        f"expected Range header, got {seen_ranges!r}"
    )
    assert (source_dir / "main.tex").exists()
    assert (source_dir / "main.tex").read_text(encoding="utf-8") == src_text


def test_download_arxiv_source_raises_tarball_corrupt_on_bad_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the byte stream completes but the resulting file isn't a
    valid gzip tarball (server returned HTML / 200 page / corrupt
    bytes), surface ``TarballCorrupt`` so the Paper Pipeline can
    fall back to the PDF path instead of aborting ingest."""
    class _Stream:
        status_code = 200

        def __enter__(self) -> "_Stream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            return iter([b"<html>Not a tarball</html>"])

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream",
        lambda *a, **k: _Stream(),
    )
    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.time.sleep", lambda _s: None,
    )

    with pytest.raises(TarballCorrupt):
        download_arxiv_source("2503.00100", cache_root=tmp_path / "cache")


def test_download_arxiv_pdf_writes_to_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF fallback writes ``<cache>/<arxiv_id>/source.pdf`` and returns
    the path. Same resume-capable downloader as the source tarball."""
    pdf_bytes = b"%PDF-1.4\n... fake pdf ...\n%%EOF\n"

    class _Stream:
        status_code = 200

        def __enter__(self) -> "_Stream":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> object:
            return iter([pdf_bytes])

    monkeypatch.setattr(
        "paperhub.pipelines.arxiv_client.httpx.stream",
        lambda *a, **k: _Stream(),
    )

    out = download_arxiv_pdf("2510.10274", cache_root=tmp_path / "cache")
    assert out == tmp_path / "cache" / "2510.10274" / "source.pdf"
    assert out.read_bytes() == pdf_bytes
