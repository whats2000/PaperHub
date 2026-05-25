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


def test_fallback_on_count_mismatch(monkeypatch) -> None:
    import paperhub.pipelines.slide_pipeline.deck_slides_map as mod
    # 3 frames but only 1 page group → mismatch (not the +1 maketitle case).
    monkeypatch.setattr(mod, "extract_frames_from_beamer",
                        lambda tex: [(1, "fA", 0, 1), (2, "fB", 2, 3), (3, "fC", 4, 5)])
    monkeypatch.setattr(mod, "map_pages_to_slides", lambda tex: ["p"])
    monkeypatch.setattr(mod, "group_logical_slides", lambda pages: [[1]])
    rows = mod.build_deck_slides("ignored", page_count=2)
    assert len(rows) == 3
    # sequential, clamped to page_count=2: pages 1, 2, 2.
    assert [(r.page_start, r.page_end) for r in rows] == [(1, 1), (2, 2), (2, 2)]


def test_fallback_page_count_zero_clamps_to_one(monkeypatch) -> None:
    import paperhub.pipelines.slide_pipeline.deck_slides_map as mod
    monkeypatch.setattr(mod, "extract_frames_from_beamer",
                        lambda tex: [(1, "fA", 0, 1), (2, "fB", 2, 3)])
    monkeypatch.setattr(mod, "map_pages_to_slides", lambda tex: [])
    monkeypatch.setattr(mod, "group_logical_slides", lambda pages: [])
    rows = mod.build_deck_slides("ignored", page_count=0)
    assert [(r.page_start, r.page_end) for r in rows] == [(1, 1), (1, 1)]
