"""Tests for the PDF-page → logical-slide mapping (F3 T9)."""

from __future__ import annotations

from paperhub.pipelines.slide_pipeline.frame_map import (
    PageSlide,
    group_logical_slides,
    map_pages_to_slides,
)


def test_maketitle_and_split_frame_mapping() -> None:
    tex = r"""
\documentclass{beamer}
\begin{document}
\maketitle
\begin{frame}{Intro}
  intro body
\end{frame}
\begin{frame}{Method}
  method part one
\end{frame}
\begin{frame}{Method}
  method part two (split, same frametitle)
\end{frame}
\begin{frame}{Results}
  results body
\end{frame}
\end{document}
"""
    pages = map_pages_to_slides(tex)
    assert len(pages) == 5

    assert pages[0] == PageSlide(page=1, frametitle=None, is_title=True)
    assert pages[1] == PageSlide(page=2, frametitle="Intro", is_title=False)
    assert pages[2] == PageSlide(page=3, frametitle="Method", is_title=False)
    assert pages[3] == PageSlide(page=4, frametitle="Method", is_title=False)
    assert pages[4] == PageSlide(page=5, frametitle="Results", is_title=False)

    assert group_logical_slides(pages) == [[1], [2], [3, 4], [5]]


def test_short_title_form_extraction() -> None:
    tex = r"""
\begin{frame}{Short Title Form}
  body
\end{frame}
"""
    pages = map_pages_to_slides(tex)
    assert len(pages) == 1
    assert pages[0].frametitle == "Short Title Form"
    assert pages[0].is_title is False


def test_frametitle_command_form_extraction() -> None:
    tex = r"""
\begin{frame}
  \frametitle{Command Form Title}
  body
\end{frame}
"""
    pages = map_pages_to_slides(tex)
    assert len(pages) == 1
    assert pages[0].frametitle == "Command Form Title"


def test_defensive_overlay_counts_two_pages() -> None:
    tex = r"""
\begin{frame}{Overlaid}
  \only<1>{first}\only<2>{second}
\end{frame}
"""
    pages = map_pages_to_slides(tex)
    assert len(pages) == 2
    assert [p.page for p in pages] == [1, 2]
    assert all(p.frametitle == "Overlaid" for p in pages)
    assert all(not p.is_title for p in pages)
    # Two consecutive pages, same frametitle → one logical group.
    assert group_logical_slides(pages) == [[1, 2]]


def test_no_title_page_first_frame_is_page_one() -> None:
    tex = r"""
\begin{frame}{First}
  body
\end{frame}
\begin{frame}{Second}
  body
\end{frame}
"""
    pages = map_pages_to_slides(tex)
    assert len(pages) == 2
    assert pages[0] == PageSlide(page=1, frametitle="First", is_title=False)
    assert pages[1] == PageSlide(page=2, frametitle="Second", is_title=False)
    assert not any(p.is_title for p in pages)
    assert group_logical_slides(pages) == [[1], [2]]


def test_titlepage_frame_detected_as_title() -> None:
    tex = r"""
\begin{frame}
  \titlepage
\end{frame}
\begin{frame}{Body}
  body
\end{frame}
"""
    pages = map_pages_to_slides(tex)
    assert len(pages) == 2
    assert pages[0] == PageSlide(page=1, frametitle=None, is_title=True)
    assert pages[1] == PageSlide(page=2, frametitle="Body", is_title=False)
    assert group_logical_slides(pages) == [[1], [2]]


def test_consecutive_none_titles_group_alone() -> None:
    tex = r"""
\begin{frame}
  body without title one
\end{frame}
\begin{frame}
  body without title two
\end{frame}
"""
    pages = map_pages_to_slides(tex)
    assert [p.frametitle for p in pages] == [None, None]
    # None titles never coalesce — each groups alone.
    assert group_logical_slides(pages) == [[1], [2]]
