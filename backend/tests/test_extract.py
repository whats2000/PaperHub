import logging
from pathlib import Path

import pymupdf
import pytest

from paperhub.pipelines.extract import (
    _extract_pdf_metadata,
    extract_latex,
    extract_pdf,
)


def _build_pdf(
    tmp_path: Path,
    *,
    title: str | None = None,
    author: str | None = None,
    creation_year: int | None = None,
) -> Path:
    """Build a 1-page PDF with optional embedded metadata fields."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    doc.new_page()
    md: dict[str, str | None] = {
        "format": "PDF 1.7",
        "title": "",
        "author": "",
        "subject": "",
        "keywords": "",
        "creator": "",
        "producer": "",
        "creationDate": "",
        "modDate": "",
        "trapped": "",
        "encryption": None,
    }
    if title is not None:
        md["title"] = title
    if author is not None:
        md["author"] = author
    if creation_year is not None:
        md["creationDate"] = f"D:{creation_year}0716120000Z00'00'"
    doc.set_metadata(md)  # type: ignore[arg-type]
    out = tmp_path / "test.pdf"
    doc.save(str(out))
    doc.close()
    return out


def test_extract_latex_finds_main_and_flattens() -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample"
    out = extract_latex(fixture)
    assert out.main_path.name == "main.tex"
    assert "Mixture-of-Experts" in out.flattened_text
    # Preamble stripped — documentclass lives before \begin{document}
    assert "\\documentclass" not in out.flattened_text
    assert "Introduction" in out.flattened_text
    assert "Method" in out.flattened_text


def test_extract_pdf_returns_text() -> None:
    fixture = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"
    text = extract_pdf(fixture)
    assert "Tiny Test Paper" in text
    assert "Mixture-of-Experts" in text


def test_extract_latex_raises_on_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_latex(tmp_path)


def test_extract_latex_follows_subdir_input(tmp_path: Path) -> None:
    """`\\input{sections/method}` must resolve when the file lives in a
    subdirectory.  This regressed silently when the tarball extractor
    flattened all members into the source root."""
    main_tex = tmp_path / "main.tex"
    main_tex.write_text(
        r"\documentclass{article}\begin{document}"
        r"\input{sections/method}"
        r"\input{sections/eval}"
        r"\end{document}",
        encoding="utf-8",
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "method.tex").write_text(
        "We propose load-balancing across experts.",
        encoding="utf-8",
    )
    (sections / "eval.tex").write_text(
        "Evaluation on MMLU and GSM8K.",
        encoding="utf-8",
    )

    out = extract_latex(tmp_path)
    assert "load-balancing" in out.flattened_text
    assert "Evaluation on MMLU" in out.flattened_text


def test_extract_latex_warns_on_missing_input(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing inputs must log a warning so a regression in the extractor or
    a malformed tarball can't silently truncate a paper to its preamble."""
    main_tex = tmp_path / "main.tex"
    main_tex.write_text(
        r"\documentclass{article}\begin{document}"
        r"\input{sections/missing}"
        r"\end{document}",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="paperhub.pipelines.extract"):
        out = extract_latex(tmp_path)

    assert any("missing" in rec.message and "sections/missing" in rec.message
               for rec in caplog.records), (
        f"expected a warning naming the missing input; got: {[r.message for r in caplog.records]}"
    )
    # Behavior unchanged on the data side — missing input still becomes empty.
    assert out.flattened_text.strip() == ""


# ---------------------------------------------------------------------------
# _extract_pdf_metadata — PDF embedded-metadata auto-detect tests
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_happy_path(tmp_path: Path) -> None:
    p = _build_pdf(
        tmp_path,
        title="A Real Paper Title",
        author="Smith, A; Lee, B",
        creation_year=2021,
    )
    md = _extract_pdf_metadata(p)
    assert md["title"] == "A Real Paper Title"
    assert md["authors"] == ["Smith, A", "Lee, B"]
    assert md["year"] == 2021


def test_extract_pdf_metadata_rejects_stock_placeholder_title(
    tmp_path: Path,
) -> None:
    for placeholder in (
        "Untitled",
        "Microsoft Word - draft.docx",
        "Layout 1",
        "Document1",
    ):
        p = _build_pdf(tmp_path, title=placeholder)
        md = _extract_pdf_metadata(p)
        assert md["title"] == "", f"{placeholder!r} should be rejected"


def test_extract_pdf_metadata_strips_whitespace_only_title(
    tmp_path: Path,
) -> None:
    p = _build_pdf(tmp_path, title="   ")
    md = _extract_pdf_metadata(p)
    assert md["title"] == ""


def test_extract_pdf_metadata_rejects_overly_long_title(
    tmp_path: Path,
) -> None:
    p = _build_pdf(tmp_path, title="x" * 501)
    md = _extract_pdf_metadata(p)
    assert md["title"] == ""


def test_extract_pdf_metadata_handles_missing_author(tmp_path: Path) -> None:
    p = _build_pdf(tmp_path, title="Paper", author=None)
    md = _extract_pdf_metadata(p)
    assert md["authors"] == []


def test_extract_pdf_metadata_splits_authors(tmp_path: Path) -> None:
    p = _build_pdf(tmp_path, title="P", author="A. Smith; B. Lee and C. Wong")
    md = _extract_pdf_metadata(p)
    assert md["authors"] == ["A. Smith", "B. Lee", "C. Wong"]


def test_extract_pdf_metadata_drops_overlong_authors(tmp_path: Path) -> None:
    p = _build_pdf(tmp_path, title="P", author="A Smith; " + ("x" * 101))
    md = _extract_pdf_metadata(p)
    assert md["authors"] == ["A Smith"]


def test_extract_pdf_metadata_extracts_creation_year(tmp_path: Path) -> None:
    p = _build_pdf(tmp_path, title="P", creation_year=2020)
    md = _extract_pdf_metadata(p)
    assert md["year"] == 2020


def test_extract_pdf_metadata_rejects_implausible_year(
    tmp_path: Path,
) -> None:
    p = _build_pdf(tmp_path, title="P", creation_year=1789)
    md = _extract_pdf_metadata(p)
    assert md["year"] is None


def test_extract_pdf_metadata_handles_empty_pdf(tmp_path: Path) -> None:
    p = _build_pdf(tmp_path)  # no metadata at all
    md = _extract_pdf_metadata(p)
    assert md["title"] == ""
    assert md["authors"] == []
    assert md["year"] is None
