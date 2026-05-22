from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
    get_frame_by_number,
    replace_frame_in_beamer,
)
from paperhub.pipelines.slide_pipeline.latex_helpers import (
    build_additional_tex,
    extract_definitions_and_usepackage_lines,
    sanitize_frametitles,
)


def test_extract_defs_and_build_additional() -> None:
    src = r"""\documentclass{article}
\usepackage{amsmath}
\newcommand{\bx}{\mathbf{x}}
\DeclareMathOperator{\softmax}{softmax}
\begin{document}\end{document}"""
    defs = extract_definitions_and_usepackage_lines(src)
    add = build_additional_tex(defs)
    assert "\\newcommand{\\bx}" in add
    assert "\\DeclareMathOperator{\\softmax}" in add


def test_frame_roundtrip() -> None:
    beamer = (
        "\\documentclass{beamer}\n\\begin{document}\n"
        "\\begin{frame}{A}\\end{frame}\n"
        "\\begin{frame}{B}\\end{frame}\n"
        "\\end{document}\n"
    )
    frames = extract_frames_from_beamer(beamer)
    assert len(frames) == 2
    assert frames[0][0] == 1
    f2 = get_frame_by_number(beamer, 2)
    assert f2 is not None and "{B}" in f2
    out = replace_frame_in_beamer(beamer, 2, "\\begin{frame}{B2}\\end{frame}")
    assert out is not None and "{B2}" in out


def test_sanitize_frametitles_escapes_ampersand() -> None:
    assert "\\&" in sanitize_frametitles("\\frametitle{Cats & Dogs}")
