"""arXiv API client: search + e-print source download.

Adapted from paper2slides-plus/src/arxiv_utils.py — extraction + download
patterns copied + edited to fit the Plan-C Paper Pipeline contract.
"""
from __future__ import annotations

import re
import tarfile
from pathlib import Path

import arxiv
import httpx
from pydantic import BaseModel

_client = arxiv.Client()

_DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# arXiv asks for a contactable User-Agent per their Terms of Use.
# https://info.arxiv.org/help/api/tou.html
_USER_AGENT = "PaperHub/0.1 (https://github.com/whats2000/PaperHub)"


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


def download_arxiv_source(arxiv_id: str, *, cache_root: Path) -> Path:
    """Download the e-print source tarball for an arxiv_id, unpack into
    cache_root / arxiv_id / source/ — all files flattened (subdirectory
    structure from the tarball is discarded), return the source directory.
    """
    target_dir = cache_root / arxiv_id
    source_dir = target_dir / "source"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Locate the source-tarball URL via the arxiv metadata query.
    search = arxiv.Search(id_list=[arxiv_id])
    result = next(iter(_client.results(search)))
    src_url = result.source_url()
    if src_url is None:
        raise RuntimeError(f"arxiv result {arxiv_id!r} has no source URL")

    # Fetch the tarball. arxiv 4.0 removed Result.download_source() — see
    # arxiv/__init__.py: source_url derives from pdf_url by swapping
    # "/pdf/" → "/src/". We download with httpx.
    tar_path = target_dir / f"{arxiv_id}.tar.gz"
    with httpx.stream(
        "GET", src_url,
        timeout=_DOWNLOAD_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as resp:
        resp.raise_for_status()
        with tar_path.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)

    source_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            # Strip leading directories; flatten into source/.
            for member in tar.getmembers():
                if member.isreg():
                    name = Path(member.name).name
                    fobj = tar.extractfile(member)
                    if fobj is None:
                        continue
                    (source_dir / name).write_bytes(fobj.read())
    finally:
        tar_path.unlink(missing_ok=True)
    return source_dir
