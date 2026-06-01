import pytest

from paperhub.agents._canvas_budget import load_canvas_budget
from paperhub.models.slide_domain import FigureDimensions, KeyFigureBundle
from paperhub.pipelines.slide_pipeline.overflow_detector import (
    classify_layout,
    count_body_tokens,
    detect_overflow,
)


def _portrait_inventory() -> dict[str, KeyFigureBundle]:
    return {
        "p0-fig-001": KeyFigureBundle(
            key="p0-fig-001",
            role="overview",
            one_line_interpretation="x",
            dimensions=FigureDimensions(width_px=600, height_px=900),  # aspect 0.667
        )
    }


def _landscape_inventory() -> dict[str, KeyFigureBundle]:
    return {
        "p0-fig-001": KeyFigureBundle(
            key="p0-fig-001",
            role="overview",
            one_line_interpretation="x",
            dimensions=FigureDimensions(width_px=1640, height_px=920),  # aspect 1.78
        )
    }


_DECK_TEXT_ONLY = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Background}
\begin{itemize}
\item Short intro point.
\item Second point.
\end{itemize}
\end{frame}
\end{document}
"""

_DECK_PORTRAIT_FIG_OVERFLOW = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Method}
\begin{columns}[T]
\begin{column}{0.5\textwidth}
\includegraphics[width=\linewidth,height=0.7\textheight,keepaspectratio]{p0-fig-001}
\end{column}
\begin{column}{0.5\textwidth}
\begin{itemize}
\item This is a deliberately very long bullet that goes on for many words to push the body well past the available budget for a half-column text region.
\item Second very long bullet equally verbose with many words crowding the canvas.
\item Third long bullet adding still more density.
\item Fourth long bullet for safe measure.
\item Fifth bullet still piling on tokens beyond the budget.
\item Sixth bullet just to be sure we exceed the budget convincingly.
\end{itemize}
\end{column}
\end{columns}
\end{frame}
\end{document}
"""


def test_count_body_tokens_strips_latex():
    n = count_body_tokens(
        r"\begin{frame}{Title}\begin{itemize}\item hello world\item foo bar\end{itemize}\end{frame}"
    )
    # Words: hello world foo bar = 4 — title isn't counted as body.
    assert n == 4


def test_count_body_tokens_ignores_includegraphics_and_label():
    n = count_body_tokens(
        r"\begin{frame}{X}\includegraphics[width=\linewidth]{foo}\label{fig:bar}hello world\end{frame}"
    )
    assert n == 2


def test_classify_layout_text_only_no_figure():
    cb = load_canvas_budget()
    layout = classify_layout(
        frame_tex=r"\begin{frame}{X}\begin{itemize}\item a\end{itemize}\end{frame}",
        figure_inventory={},
        canvas_budget=cb,
    )
    assert layout.name == "text_only"


def test_classify_layout_columns_with_portrait_figure():
    cb = load_canvas_budget()
    layout = classify_layout(
        frame_tex=_DECK_PORTRAIT_FIG_OVERFLOW.split(r"\begin{document}")[1].split(r"\end{document}")[0],
        figure_inventory=_portrait_inventory(),
        canvas_budget=cb,
    )
    # 0.5\textwidth column → figure_left_half_portrait
    assert layout.name in ("figure_left_half_portrait", "figure_right_half_portrait")


def test_detect_overflow_flags_overcrammed_frame():
    cb = load_canvas_budget()
    signals = detect_overflow(
        deck_tex=_DECK_PORTRAIT_FIG_OVERFLOW,
        figure_inventory=_portrait_inventory(),
        canvas_budget=cb,
        pdflatex_log="",
        script="en",
    )
    assert len(signals) == 1
    sig = signals[0]
    assert sig.exceeds_canvas_budget is True
    assert sig.overage_tokens > 0
    assert sig.recommendation in ("split_frame", "tighten", "shrink_figure")


def test_detect_overflow_clean_frame_under_budget():
    cb = load_canvas_budget()
    signals = detect_overflow(
        deck_tex=_DECK_TEXT_ONLY,
        figure_inventory={},
        canvas_budget=cb,
        pdflatex_log="",
        script="en",
    )
    assert len(signals) == 1
    assert signals[0].exceeds_canvas_budget is False
    assert signals[0].recommendation == "ok"


def test_detect_overflow_aspect_mismatch_flagged():
    cb = load_canvas_budget()
    # Use the column-layout frame BUT swap the inventory to a landscape figure
    # — that's a layout_aspect_mismatch (landscape figure stuffed into a portrait slot).
    signals = detect_overflow(
        deck_tex=_DECK_PORTRAIT_FIG_OVERFLOW,
        figure_inventory=_landscape_inventory(),
        canvas_budget=cb,
        pdflatex_log="",
        script="en",
    )
    assert signals[0].layout_aspect_mismatch is True


def test_count_visual_tokens_short_bullet_clamps_to_one_line():
    """A 3-word bullet on a wide column still counts as at least one visual line."""
    from paperhub.agents._canvas_budget import load_canvas_budget
    from paperhub.pipelines.slide_pipeline.overflow_detector import _count_visual_tokens

    cb = load_canvas_budget()
    text_only = next(layout for layout in cb.layouts if layout.name == "text_only")
    # "Item one" = 2 words; one visual line on 12.8cm wide @ en density.
    # Should NOT be 0; should be ~25 tokens-per-line worth of capacity used.
    n = _count_visual_tokens(
        frame_tex=r"\begin{frame}{X}\begin{itemize}\item Item one\end{itemize}\end{frame}",
        layout=text_only,
        constants=cb.constants,
        script="en",
    )
    # One visual line at ~25 tokens/line = ~25.
    assert n >= 20 and n <= 30


def test_count_visual_tokens_long_bullet_rounds_up_to_multiple_lines():
    """A 28-word bullet on a 6.8cm half-column wraps to ~3 visual lines."""
    from paperhub.agents._canvas_budget import load_canvas_budget
    from paperhub.pipelines.slide_pipeline.overflow_detector import _count_visual_tokens

    cb = load_canvas_budget()
    half = next(layout for layout in cb.layouts if layout.name == "figure_left_half_portrait")
    # 6.8cm × 12 chars/cm / 6 chars/word ≈ 13 tokens per visual line.
    # 28-word bullet → 28/13 = 2.15 → rounds UP to 3 visual lines = ~39 tokens.
    long_bullet = " ".join(["word"] * 28)
    n = _count_visual_tokens(
        frame_tex=rf"\begin{{frame}}{{X}}\begin{{itemize}}\item {long_bullet}\end{{itemize}}\end{{frame}}",
        layout=half,
        constants=cb.constants,
        script="en",
    )
    # 3 visual lines × ~13 tokens/line ≈ 39.
    assert n >= 30 and n <= 50


def test_count_visual_tokens_cjk_density_differs_from_en():
    """The script param must actually feed the chars_per_cm lookup.

    This is the bug-fix regression test: before threading ``script`` through,
    ``_count_visual_tokens`` hardcoded ``chars_per_cm["en"]``, so en and cjk
    returned IDENTICAL counts for the same bullet — even though the budget
    side (``compute_token_budget``) used the script-correct density. That
    asymmetry produced wrong overflow signals for CJK frames.

    With the fix, cjk uses a smaller per-line capacity (8 chars/cm vs 12 for
    en), so the wrap math differs and the totals diverge.
    """
    from paperhub.agents._canvas_budget import load_canvas_budget
    from paperhub.pipelines.slide_pipeline.overflow_detector import _count_visual_tokens

    cb = load_canvas_budget()
    half = next(layout for layout in cb.layouts if layout.name == "figure_left_half_portrait")
    long_bullet = " ".join(["word"] * 28)
    frame = rf"\begin{{frame}}{{X}}\begin{{itemize}}\item {long_bullet}\end{{itemize}}\end{{frame}}"
    en = _count_visual_tokens(frame_tex=frame, layout=half, constants=cb.constants, script="en")
    cjk = _count_visual_tokens(frame_tex=frame, layout=half, constants=cb.constants, script="cjk")
    # Pre-fix bug: both returned 39 because the hardcoded "en" density ignored
    # the script argument. With the fix, the two must diverge.
    assert en != cjk, (
        f"script param did not affect the count: en={en}, cjk={cjk} — "
        "the chars_per_cm lookup is still hardcoded to 'en'."
    )


def test_detect_overflow_parses_pdflatex_overfull_log():
    cb = load_canvas_budget()
    log = (
        "Overfull \\vbox (23.7pt too high) detected at line 12.\n"
        "...lots of other latex noise..."
    )
    signals = detect_overflow(
        deck_tex=_DECK_TEXT_ONLY,
        figure_inventory={},
        canvas_budget=cb,
        pdflatex_log=log,
        script="en",
    )
    assert signals[0].pdflatex_overfull_pt == pytest.approx(23.7, rel=1e-3)
