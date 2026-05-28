import logging
from pathlib import Path

import pymupdf
import pytest

from paperhub.pipelines.extract import (
    _extract_pdf_metadata,
    _extract_pdf_title_from_page1,
    extract_latex,
    extract_pdf,
    extract_pdf_with_headings,
)


def _build_pdf(
    tmp_path: Path,
    *,
    title: str | None = None,
    author: str | None = None,
    creation_year: int | None = None,
    page1_lines: list[tuple[str, float]] | None = None,
) -> Path:
    """Build a 1-page PDF with optional embedded metadata fields.

    ``page1_lines`` is an optional list of ``(text, font_size)`` tuples
    rendered top-to-bottom on page 1 — used to exercise the page-1
    largest-font title-recovery heuristic.
    """
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    if page1_lines:
        y = 72.0
        for text, size in page1_lines:
            page.insert_text((72, y), text, fontsize=size)
            y += size + 8  # advance below the line
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


def test_extract_pdf_with_headings_detects_font_size_band(tmp_path: Path) -> None:
    """Headings are lines whose font size sits in the band between the modal
    body size and the page-1 title size. The title (largest) is excluded;
    body text (smallest, most common) is excluded; section headings in
    between are returned with char offsets into the returned full_text."""
    pdf = _build_pdf(
        tmp_path,
        page1_lines=[
            ("Turning the TIDE: Cross-Architecture Distillation", 18.0),  # title
            ("Abstract", 14.0),  # heading
            ("This paper studies distillation across architectures in depth.", 11.0),
            ("Introduction", 14.0),  # heading
            ("Diffusion language models have grown rapidly over recent years.", 11.0),
            ("Methods", 14.0),  # heading
            ("We train a student model with a cross-architecture objective here.", 11.0),
        ],
    )
    full_text, headings = extract_pdf_with_headings(pdf)
    names = [h[0] for h in headings]
    assert "Abstract" in names
    assert "Introduction" in names
    assert "Methods" in names
    # Title (largest font) is NOT treated as a heading.
    assert not any("Turning the TIDE" in n for n in names)
    # Offsets point at the heading text inside full_text.
    for name, off in headings:
        assert full_text[off : off + len(name)] == name


def test_extract_pdf_with_headings_flat_font_returns_empty(tmp_path: Path) -> None:
    """A PDF with a single uniform font (no size contrast) yields no
    detectable headings — the caller falls back to a single synthetic
    section covering the whole document."""
    pdf = _build_pdf(
        tmp_path,
        page1_lines=[
            ("Some uniform text line one here.", 11.0),
            ("Some uniform text line two here.", 11.0),
            ("Some uniform text line three here.", 11.0),
        ],
    )
    full_text, headings = extract_pdf_with_headings(pdf)
    assert full_text.strip() != ""
    assert headings == []


def test_extract_latex_ignores_commented_begin_document(tmp_path: Path) -> None:
    """Regression: arXiv:2503.07137 (MoE survey, IEEE class) shipped its
    main.tex with a template stub ``%\\begin{document}`` *before* the real
    ``\\begin{document}``. ``re.search`` is a substring match — it hit the
    commented stub first, sliced the preamble at that point, and left the
    REAL ``\\begin{document}`` in the flattened body. The matching real
    ``\\end{document}`` was then stripped (only one of them in the source),
    so pandoc saw a document that opens but never closes and exited with
    ``unexpected end of input``. The renderer's pylatexenc fallback wraps
    everything in ``<pre>`` — that's why the Citation Canvas showed plain
    text. Skip comment-line matches: only the real markers should drive
    the strip."""
    (tmp_path / "main.tex").write_text(
        "\\documentclass{IEEEtran}\n"
        "% Example template the IEEEtran class ships with:\n"
        "%\\begin{document}\n"
        "% \\title{Example}\n"
        "%\\end{document}\n"
        "\n"
        "\\begin{document}\n"
        "The real body content.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    out = extract_latex(tmp_path)
    assert "\\begin{document}" not in out.flattened_text, (
        f"real \\begin{{document}} leaked into the body: "
        f"{out.flattened_text!r}"
    )
    assert "\\end{document}" not in out.flattened_text
    assert "The real body content." in out.flattened_text


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


# ---------------------------------------------------------------------------
# _extract_pdf_title_from_page1 — page-1 largest-font fallback tests
# ---------------------------------------------------------------------------


def test_page1_heuristic_extracts_largest_font_text(tmp_path: Path) -> None:
    p = _build_pdf(
        tmp_path,
        page1_lines=[
            ("YOLOSeg for wafer defect segmentation", 26),
            ("Yen-Ting Li, Yu-Cheng Chan", 10),
            ("This study develops the you only look once...", 9),
        ],
    )
    title = _extract_pdf_title_from_page1(p)
    assert title == "YOLOSeg for wafer defect segmentation"


def test_page1_heuristic_joins_multi_line_titles(tmp_path: Path) -> None:
    p = _build_pdf(
        tmp_path,
        page1_lines=[
            ("YOLOSeg with applications", 26),
            ("to wafer die particle defect segmentation", 26),
            ("Abstract: ...", 9),
        ],
    )
    title = _extract_pdf_title_from_page1(p)
    assert title == (
        "YOLOSeg with applications to wafer die particle defect segmentation"
    )


def test_page1_heuristic_returns_empty_when_no_text(tmp_path: Path) -> None:
    p = _build_pdf(tmp_path)  # no page1_lines, no metadata
    assert _extract_pdf_title_from_page1(p) == ""


def test_page1_heuristic_runs_through_sanitiser(tmp_path: Path) -> None:
    # "Untitled" at the largest font should still be rejected by the
    # placeholder sanitiser.
    p = _build_pdf(
        tmp_path,
        page1_lines=[
            ("Untitled", 26),
            ("body text", 10),
        ],
    )
    assert _extract_pdf_title_from_page1(p) == ""


def test_extract_pdf_metadata_falls_back_to_page1_when_metadata_title_missing(
    tmp_path: Path,
) -> None:
    # Empty metadata title, but a clear page-1 title at large font.
    p = _build_pdf(
        tmp_path,
        title="",  # explicit empty
        author="A. Smith",
        creation_year=2025,
        page1_lines=[
            ("Recovered Title From Page One", 24),
            ("authors here", 10),
            ("body", 9),
        ],
    )
    md = _extract_pdf_metadata(p)
    assert md["title"] == "Recovered Title From Page One"
    # Other fields still come from the embedded metadata.
    assert md["authors"] == ["A. Smith"]
    assert md["year"] == 2025


def test_extract_pdf_metadata_prefers_metadata_title_over_page1_when_both_present(
    tmp_path: Path,
) -> None:
    p = _build_pdf(
        tmp_path,
        title="Title From Metadata",
        page1_lines=[("Different Title On Page One", 26)],
    )
    md = _extract_pdf_metadata(p)
    assert md["title"] == "Title From Metadata"  # metadata wins
