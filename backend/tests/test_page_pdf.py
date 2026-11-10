"""Every paper has a page PDF: its own, or one compiled from its LaTeX."""
from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pymupdf
import pytest
from PIL import Image

from paperhub.pipelines import page_pdf
from paperhub.pipelines.page_pdf import (
    COMPILED_PDF,
    FAILED_MARKER,
    compile_paper_pdf,
    ensure_page_pdf,
    fetch_arxiv_pdf,
    page_pdf_path,
)
from paperhub.pipelines.paper_asset import paper_asset_dir

_HAS_PDFLATEX = shutil.which("pdflatex") is not None


def _pdf(path: Path, pages: int = 1) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)


def test_page_pdf_prefers_the_papers_own_pdf(tmp_path: Path) -> None:
    assert page_pdf_path(tmp_path) is None
    (paper_asset_dir(tmp_path)).mkdir()
    _pdf(paper_asset_dir(tmp_path) / COMPILED_PDF)
    assert page_pdf_path(tmp_path) == paper_asset_dir(tmp_path) / COMPILED_PDF
    _pdf(tmp_path / "x.pdf")
    assert page_pdf_path(tmp_path) == tmp_path / "x.pdf"


def _tex_source(root: Path, body: str) -> Path:
    src = root / "source"
    src.mkdir(parents=True)
    Image.new("RGB", (40, 20), (0, 90, 0)).save(src / "fig.png")
    main = src / "main.tex"
    main.write_text("\\documentclass{article}\n\\usepackage{graphicx}\n"
                    "\\begin{document}\n" + body + "\n\\end{document}\n", encoding="utf-8")
    return main


@pytest.mark.skipif(not _HAS_PDFLATEX, reason="pdflatex not installed")
def test_compile_writes_asset_pdf(tmp_path: Path) -> None:
    main = _tex_source(tmp_path, "Page one.\n\\begin{figure}\\includegraphics{fig}"
                                 "\\caption{A figure.}\\end{figure}\n\\newpage Page two.")
    out = compile_paper_pdf(main, tmp_path)
    assert out == paper_asset_dir(tmp_path) / COMPILED_PDF
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
    assert not list(tmp_path.glob("*.pdf"))        # never at the cache root
    assert not list(main.parent.glob("*.aux"))     # the source tree is untouched


@pytest.mark.skipif(not _HAS_PDFLATEX, reason="pdflatex not installed")
def test_compile_failure_returns_none_and_leaves_no_partial_file(tmp_path: Path) -> None:
    main = _tex_source(tmp_path, "\\undefinedcommandxyz")
    assert compile_paper_pdf(main, tmp_path) is None
    asset = paper_asset_dir(tmp_path)
    assert not (asset / COMPILED_PDF).exists()
    assert not [p for p in asset.glob("*") if p.suffix in (".pdf", ".tmp")] if asset.exists() \
        else True


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://arxiv.org/pdf/2401.00001")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(status, request=req))


def test_arxiv_fallback_runs_once_when_the_pdf_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def missing(url: str, target: Path) -> None:
        calls.append(url)
        raise _http_error(404)

    monkeypatch.setattr(page_pdf, "_download", missing)
    assert fetch_arxiv_pdf("2401.00001", tmp_path) is None
    assert (paper_asset_dir(tmp_path) / FAILED_MARKER).is_file()
    assert fetch_arxiv_pdf("2401.00001", tmp_path) is None
    assert len(calls) == 1


def test_a_transient_arxiv_failure_is_retried_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate limit, server error or network drop is not a verdict on the
    paper: no marker, so the next backfill tries once more."""
    calls: list[str] = []

    def flaky(url: str, target: Path) -> None:
        calls.append(url)
        if len(calls) == 1:
            raise _http_error(429)
        if len(calls) == 2:
            raise httpx.ConnectError("down")
        _pdf(target, pages=2)

    monkeypatch.setattr(page_pdf, "_download", flaky)
    assert fetch_arxiv_pdf("2401.00001", tmp_path) is None
    assert not (paper_asset_dir(tmp_path) / FAILED_MARKER).exists()
    assert fetch_arxiv_pdf("2401.00001", tmp_path) is None
    assert fetch_arxiv_pdf("2401.00001", tmp_path) == paper_asset_dir(tmp_path) / COMPILED_PDF
    assert len(calls) == 3


def test_arxiv_fetch_writes_asset_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(url: str, target: Path) -> None:
        assert url == "https://arxiv.org/pdf/2401.00001"
        _pdf(target, pages=3)

    monkeypatch.setattr(page_pdf, "_download", ok)
    out = fetch_arxiv_pdf("2401.00001", tmp_path)
    assert out == paper_asset_dir(tmp_path) / COMPILED_PDF
    assert not list(tmp_path.glob("*.pdf"))


def test_ensure_page_pdf_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def no_compile(main: Path, cache: Path, **_: object) -> None:
        seen.append("compile")
        return None

    def fetched(arxiv_id: str, cache: Path) -> Path:
        seen.append("fetch")
        paper_asset_dir(cache).mkdir(parents=True, exist_ok=True)
        out = paper_asset_dir(cache) / COMPILED_PDF
        _pdf(out)
        return out

    monkeypatch.setattr(page_pdf, "compile_paper_pdf", no_compile)
    monkeypatch.setattr(page_pdf, "fetch_arxiv_pdf", fetched)
    out = ensure_page_pdf(tmp_path / "main.tex", "2401.00001", tmp_path)
    assert out is not None and seen == ["compile", "fetch"]
    seen.clear()
    assert ensure_page_pdf(tmp_path / "main.tex", "2401.00001", tmp_path) == out
    assert seen == []                               # already there: nothing runs
    other = tmp_path / "upload"
    other.mkdir()
    assert ensure_page_pdf(None, None, other) is None   # LaTeX upload, no source / id


def test_an_upload_named_paper_pdf_is_the_papers_own_pdf(tmp_path: Path) -> None:
    """Compiled means asset/paper.pdf, not any file called paper.pdf."""
    from paperhub.pipelines.page_pdf import is_compiled_pdf

    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "paper.pdf")              # a user's upload, kept by name
    own = page_pdf_path(tmp_path)
    assert own == tmp_path / "paper.pdf" and not is_compiled_pdf(own)
    (tmp_path / "asset").mkdir()
    doc.save(tmp_path / "asset" / "paper.pdf")
    assert is_compiled_pdf(tmp_path / "asset" / "paper.pdf")


def test_pdflatex_runs_without_shell_escape_and_paranoid_file_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paper sources are third-party: no shell escape, and TeX may read and
    write only below its working directory (not e.g. backend/.env)."""
    seen: list[tuple[list[str], dict[str, str]]] = []

    def run(cmd: list[str], **kw: object) -> object:
        env = kw.get("env")
        assert isinstance(env, dict)
        seen.append((cmd, env))
        raise OSError("no pdflatex here")

    src = tmp_path / "src"
    src.mkdir()
    (src / "main.tex").write_text("\documentclass{article}", encoding="utf-8")
    monkeypatch.setattr(page_pdf.subprocess, "run", run)
    assert compile_paper_pdf(src / "main.tex", tmp_path / "cache") is None
    cmd, env = seen[0]
    assert "-no-shell-escape" in cmd
    assert env["openin_any"] == "p" and env["openout_any"] == "p"
