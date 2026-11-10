"""paperhub-backfill-page-pdfs: older papers get a page PDF and figure places."""
from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from paperhub.cli.backfill_page_pdfs import process_paper, verify_paper
from paperhub.pipelines.page_pdf import COMPILED_PDF
from paperhub.pipelines.paper_asset import (
    FigureAsset,
    PaperAsset,
    paper_asset_dir,
    write_paper_asset,
)

_HAS_PDFLATEX = shutil.which("pdflatex") is not None


def _latex_paper(root: Path, caption: str) -> Path:
    """A cache dir with a LaTeX source, its asset and no page PDF; returns main.tex."""
    src = root / "source"
    src.mkdir(parents=True)
    Image.new("RGB", (300, 200), (0, 90, 0)).save(src / "fig.png")
    main = src / "main.tex"
    main.write_text(
        "\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n"
        "Some text.\n\\begin{figure}\\centering\\includegraphics[width=0.6\\linewidth]{fig}"
        f"\\caption{{{caption}}}\\end{{figure}}\n\\end{{document}}\n", encoding="utf-8")
    asset = paper_asset_dir(root)
    (asset / "figures").mkdir(parents=True)
    shutil.copy(src / "fig.png", asset / "figures" / "fig-001.png")
    write_paper_asset(PaperAsset(figures=[FigureAsset(
        "fig-001", caption, None, None, "figures/fig-001.png", env_index=0)]), root)
    return main


@pytest.mark.skipif(not _HAS_PDFLATEX, reason="pdflatex not installed")
def test_compiles_places_and_is_idempotent(tmp_path: Path) -> None:
    main = _latex_paper(tmp_path, "The whole training pipeline of our method.")
    first = process_paper("arxiv", str(main), "2401.00001", tmp_path, dry_run=False)
    assert first.status == "compiled" and first.placed == ["fig-001"]
    assert (paper_asset_dir(tmp_path) / COMPILED_PDF).is_file()
    again = process_paper("arxiv", str(main), "2401.00001", tmp_path, dry_run=False)
    assert again.status == "unchanged" and again.placed == []
    assert verify_paper(tmp_path) == []


def test_pdf_paper_with_its_own_pdf_is_unchanged(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "source.pdf")
    result = process_paper("pdf_upload", str(tmp_path / "source.pdf"), None, tmp_path,
                           dry_run=False)
    assert result.status == "unchanged"


def test_missing_source_is_reported(tmp_path: Path) -> None:
    result = process_paper("arxiv", None, None, tmp_path, dry_run=False)
    assert result.status == "no_source"
    assert verify_paper(tmp_path) == ["no page PDF"]


def test_dry_run_compiles_nothing(tmp_path: Path) -> None:
    main = _latex_paper(tmp_path, "The whole training pipeline of our method.")
    result = process_paper("arxiv", str(main), "2401.00001", tmp_path, dry_run=True)
    assert result.status == "would_compile"
    assert not (paper_asset_dir(tmp_path) / COMPILED_PDF).exists()


@pytest.mark.skipif(not _HAS_PDFLATEX, reason="pdflatex not installed")
def test_verify_warns_about_an_unplaced_figure(tmp_path: Path) -> None:
    main = _latex_paper(tmp_path, "The whole training pipeline of our method.")
    process_paper("arxiv", str(main), None, tmp_path, dry_run=False)
    asset_json = paper_asset_dir(tmp_path) / "figures.json"
    text = asset_json.read_text(encoding="utf-8").replace(
        "The whole training pipeline of our method.", "A caption that is nowhere.")
    asset_json.write_text(text, encoding="utf-8")
    # A different image size too, so the image-size fallback cannot place it.
    Image.new("RGB", (31, 17)).save(paper_asset_dir(tmp_path) / "figures" / "fig-001.png")
    # Re-placing finds nothing for the renamed caption; the place is cleared.
    process_paper("arxiv", str(main), None, tmp_path, dry_run=False, replace_places=True)
    assert verify_paper(tmp_path) == []          # a page PDF exists: no failure
    from paperhub.cli.backfill_page_pdfs import unplaced_figures
    assert unplaced_figures(tmp_path) == ["fig-001"]


def test_no_arxiv_never_fetches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from paperhub.cli import backfill_page_pdfs as cli

    monkeypatch.setattr(cli, "compile_paper_pdf", lambda *a, **k: None)
    called: list[str] = []
    monkeypatch.setattr(cli, "fetch_arxiv_pdf", lambda *a, **k: called.append("x"))
    main = tmp_path / "main.tex"
    main.write_text("x", encoding="utf-8")
    result = process_paper("arxiv", str(main), "2401.00001", tmp_path, dry_run=False,
                           allow_fetch=False)
    assert result.status == "failed" and called == []


@pytest.mark.skipif(not _HAS_PDFLATEX, reason="pdflatex not installed")
def test_replace_places_writes_the_asset_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-placing computes every place first and writes once, so a reader
    never sees the asset with its places cleared."""
    from paperhub.cli import backfill_page_pdfs as cli

    main = _latex_paper(tmp_path, "The whole training pipeline of our method.")
    process_paper("arxiv", str(main), None, tmp_path, dry_run=False)
    writes: list[int] = []
    real = cli.write_paper_asset

    def counting(asset: object, cache_dir: Path) -> object:
        writes.append(1)
        return real(asset, cache_dir)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "write_paper_asset", counting)
    process_paper("arxiv", str(main), None, tmp_path, dry_run=False, replace_places=True)
    assert len(writes) <= 1                      # never a cleared-then-placed pair
    from paperhub.cli.backfill_page_pdfs import unplaced_figures
    assert unplaced_figures(tmp_path) == []


def test_replace_places_flag_reaches_process_paper() -> None:
    from paperhub.cli.backfill_page_pdfs import _parser

    assert _parser().parse_args(["--replace-places"]).replace_places is True
    assert _parser().parse_args([]).replace_places is False
