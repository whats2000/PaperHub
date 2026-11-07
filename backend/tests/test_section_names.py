"""Section names must read as headings (LaTeX macros, PDF / Marker fragments)."""
from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from paperhub.pipelines.chunker import chunk_text
from paperhub.pipelines.extract import extract_pdf_with_headings
from paperhub.pipelines.marker_client import MarkerBlock
from paperhub.pipelines.marker_to_asset import _build_section_name_map
from paperhub.pipelines.section_names import clean_latex_heading, is_heading_shaped

# --- clean_latex_heading ------------------------------------------------------

_MACROS: dict[str, str | list[object]] = {
    "molmoactt": r"\textsc{MolmoAct2}\xspace",
    "modelname": "GR00T N1",
    "algo": "MDLM",
    "ours": r"\modelname{}\xspace",
    "method": [r"\textbf{#1}-Net", 1],
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"\molmoactt", "MolmoAct2"),  # paper 15
        (r"\modelname{} Foundation Model", "GR00T N1 Foundation Model"),  # paper 25
        (r"\algo{}", "MDLM"),  # paper 71
        (r"\ours vs. baselines", "GR00T N1 vs. baselines"),  # nested macro
        (r"\method{Fast} design", "Fast-Net design"),  # macro with an argument
        (
            r"\texorpdfstring{Insensitivity to $\lambda_0$ and $\alpha$}"
            r"{Insensitivity to lambda\_0 and alpha}",
            "Insensitivity to lambda_0 and alpha",
        ),  # paper 53
        (r"Shared vs.\ Modality-Specific FFNs", "Shared vs. Modality-Specific FFNs"),  # 74
        (r"Results\label{sec:results}", "Results"),
        (r"Data \& Code~Release", "Data & Code Release"),
        (r"Scaling with $N$ \emph{experts}", "Scaling with $N$ experts"),
        (r"Related Work\footnote{See also \cite{x}.}", "Related Work"),
    ],
)
def test_clean_latex_heading(raw: str, expected: str) -> None:
    assert clean_latex_heading(raw, _MACROS) == expected


def test_clean_latex_heading_plain_is_unchanged() -> None:
    for plain in ("Introduction", "Related Work", "A.1 Proof of Lemma 3", "Results: Part 2"):
        assert clean_latex_heading(plain, _MACROS) == plain


def test_clean_latex_heading_unknown_macro_keeps_argument_text() -> None:
    assert clean_latex_heading(r"\unknownfmt{Robust} Training", {}) == "Robust Training"
    # An unknown bare macro carries no recoverable text: it is dropped.
    assert clean_latex_heading(r"\xspace Ablations", {}) == "Ablations"


def test_clean_latex_heading_never_empty() -> None:
    assert clean_latex_heading(r"\undefinedmacro", {}) == r"\undefinedmacro"


# --- chunker integration --------------------------------------------------------

_TEX = (
    "Preamble text.\n\n"
    "\\section{Introduction}\nWe study robots.\n\n"
    "\\section{\\modelname{} Foundation Model}\nThe model has two systems.\n\n"
    "\\section{\\algo{}}\nMasked diffusion.\n"
)


def test_chunker_names_sections_from_balanced_cleaned_argument() -> None:
    chunks = chunk_text(_TEX, macros=_MACROS)
    assert [c.section for c in chunks] == [
        None, "Introduction", "GR00T N1 Foundation Model", "MDLM",
    ]


def test_chunker_section_fix_keeps_chunk_boundaries() -> None:
    """Only names change: offsets and text match the pre-fix span split
    (which ends at the first ``}``), so stored chunk ids / citations stay valid."""
    fixed = chunk_text(_TEX, macros=_MACROS)
    plain = chunk_text(_TEX)
    assert [(c.char_start, c.char_end, c.text) for c in fixed] == [
        (c.char_start, c.char_end, c.text) for c in plain
    ]
    # The pre-fix span split started right after `\section{\modelname{}`.
    assert fixed[2].text.startswith("Foundation Model}")


# --- is_heading_shaped -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Introduction", "Methods", "2.1 Methodology", "E.1 Frozen Evaluation.",
        "A.4.1 Effect of Data Curation", "Materials & experimental systems",
        "Literature on wafer and die/chip defect segmentation", "Data Availability",
        "V-JEPA 2: Self-Supervised Video Models", "研究方法",
    ],
)
def test_is_heading_shaped_accepts_headings(name: str) -> None:
    assert is_heading_shaped(name)


@pytest.mark.parametrize(
    "name",
    [
        "--alignIntronMax 1000000 \\",  # shell continuation (paper 66)
        "STAR --runMode alignReads \\",
        "gene with the highest y_proba_full_model score.",  # lowercase prose
        "Each upres_block takes the output from the previous stage",  # identifier
        "Transformer Tower section). Each pair_update_block first computes",
        "(b) Reach with Cup",  # sub-figure label (paper 35)
        "100 200 300 400 500 600 700 800 900 81 81.5 82 VM22M",  # chart axis
        "//data.4dnucleome.org/, accessed 2021/03/04), aiming for quality",
        "\ufffdE For tasks evaluated with auPRC",
        "x",
    ],
)
def test_is_heading_shaped_rejects_fragments(name: str) -> None:
    assert not is_heading_shaped(name)


# --- PDF heading heuristic -----------------------------------------------------------


def _pdf(tmp_path: Path, lines: list[list[tuple[str, float]]]) -> Path:
    """One page; each line is a list of (text, size) runs drawn left to right."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    y = 72.0
    for runs in lines:
        x = 72.0
        for text, size in runs:
            page.insert_text((x, y), text, fontsize=size)
            x += pymupdf.get_text_length(text, fontsize=size)
        y += max(s for _, s in runs) + 8
    out = tmp_path / "doc.pdf"
    doc.save(str(out))
    doc.close()
    return out


def test_pdf_fragment_candidates_keep_boundary_but_inherit_real_heading(
    tmp_path: Path,
) -> None:
    """A prose line carrying one larger inline span, or a code line, is a
    font-size candidate but not a heading: its boundary is kept (so chunk
    offsets never move) and it carries the enclosing real heading's name."""
    pdf = _pdf(tmp_path, [
        [("A Title Of The Paper", 20.0)],
        [("Introduction", 14.0)],
        [("Body text sentence number one is here for the body.", 10.0)],
        [("Body text sentence number two is here for the body.", 10.0)],
        [("Methods", 14.0)],
        [("The block ", 10.0), ("conv_block", 14.0), (" applies a convolution.", 10.0)],
        [("Body text sentence number three is here for the body.", 10.0)],
        [("--genomeDir data \\", 14.0)],
        [("Body text sentence number four is here for the body.", 10.0)],
    ])
    full_text, headings = extract_pdf_with_headings(pdf)
    names = [n for n, _ in headings]
    assert names == ["Introduction", "Methods", "Methods", "Methods"]
    offsets = [o for _, o in headings]
    assert offsets == sorted(offsets)
    assert full_text[offsets[2]:].startswith("The block")
    assert full_text[offsets[3]:].startswith("--genomeDir")


def test_pdf_leading_fragment_before_any_heading_is_dropped(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path, [
        [("A Title Of The Paper", 20.0)],
        [("see the ", 10.0), ("helper_fn", 14.0), (" below for details.", 10.0)],
        [("Body text sentence number one is here for the body.", 10.0)],
        [("Introduction", 14.0)],
        [("Body text sentence number two is here for the body.", 10.0)],
    ])
    _, headings = extract_pdf_with_headings(pdf)
    assert [n for n, _ in headings] == ["Introduction"]


# --- Marker SectionHeader names ---------------------------------------------------


def _hdr(bid: str, text: str) -> MarkerBlock:
    return MarkerBlock(block_type="SectionHeader", html=f"<h2>{text}</h2>", block_id=bid)


def test_marker_name_map_drops_fragments_and_joins_wrapped_headings() -> None:
    blocks = [
        _hdr("/page/0/SectionHeader/0", "Literature on generative image augmentation for"),
        _hdr("/page/0/SectionHeader/1", "applications"),
        MarkerBlock(block_type="Text", html="<p>Body.</p>", block_id="/page/0/Text/2"),
        _hdr("/page/1/SectionHeader/3", "(b) Reach with Cup"),
        _hdr("/page/1/SectionHeader/4", "100 200 300 400 500 81 81.5 82"),
        _hdr("/page/1/SectionHeader/5", "Methodology"),
    ]
    name_map = _build_section_name_map(blocks)
    joined = "Literature on generative image augmentation for applications"
    assert name_map == {
        "/page/0/SectionHeader/0": joined,
        "/page/0/SectionHeader/1": joined,
        "/page/1/SectionHeader/5": "Methodology",
    }


def test_pdf_wrapped_heading_second_line_is_joined(tmp_path: Path) -> None:
    """A heading wrapped over two lines (paper 24's "... segmentation" +
    "applications") names both boundaries with the joined heading."""
    pdf = _pdf(tmp_path, [
        [("A Title Of The Paper", 20.0)],
        [("Introduction", 14.0)],
        [("Body text sentence number one is here for the body.", 10.0)],
        [("Literature on image augmentation for defect", 14.0)],
        [("segmentation applications", 14.0)],
        [("Body text sentence number two is here for the body.", 10.0)],
    ])
    _, headings = extract_pdf_with_headings(pdf)
    joined = "Literature on image augmentation for defect segmentation applications"
    assert [n for n, _ in headings] == ["Introduction", joined, joined]
