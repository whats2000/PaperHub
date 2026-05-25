from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides

_DECK = (
    "\\documentclass{beamer}\n\\begin{document}\n"
    "\\begin{frame}\\titlepage\\end{frame}\n"
    "\\begin{frame}{Intro}\\begin{itemize}\\item a\\end{itemize}\\end{frame}\n"
    "\\begin{frame}{Method}\\begin{itemize}\\item b\\end{itemize}\\end{frame}\n"
    "\\end{document}\n"
)


def test_one_to_one_pages() -> None:
    rows = build_deck_slides(_DECK, page_count=3)
    assert [r.slide_index for r in rows] == [0, 1, 2]
    assert [(r.page_start, r.page_end) for r in rows] == [(1, 1), (2, 2), (3, 3)]
    assert "Intro" in rows[1].frame_tex


def test_leading_maketitle_offsets_pages() -> None:
    deck = (
        "\\documentclass{beamer}\n\\begin{document}\n\\maketitle\n"
        "\\begin{frame}{Intro}\\item a\\end{frame}\n"
        "\\begin{frame}{Method}\\item b\\end{frame}\n\\end{document}\n"
    )
    rows = build_deck_slides(deck, page_count=3)
    assert len(rows) == 2
    assert [(r.page_start, r.page_end) for r in rows] == [(2, 2), (3, 3)]
    assert "Intro" in rows[0].frame_tex
