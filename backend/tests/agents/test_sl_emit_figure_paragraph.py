"""F4.5 — defensive post-process that wraps ``\\includegraphics`` in a
``\\begin{center}...\\end{center}`` environment (explicit scope, no
declaration-leak into surrounding caption text) and injects a blank line
after the figure block so the following text breaks onto its own paragraph.

Without it, CJK decks rendered caption text inline to the RIGHT of the figure
instead of below — and the earlier ``\\centering``-based fix leaked declaration
state across the rest of the frame body, so the bug recurred.
"""
from paperhub.agents.sl_emit import enforce_figure_paragraph_break


def test_injects_par_when_text_follows_includegraphics_inline() -> None:
    """The Chinese-deck failure: \\includegraphics + \\vspace + text on next non-empty line."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"  \includegraphics[width=\linewidth,height=0.6\textheight,keepaspectratio]{p0-fig-001}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"  {\small Caption text.}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    # Must wrap the figure in \begin{center}...\end{center}.
    begin_idx = fixed.find("\\begin{center}")
    fig_idx = fixed.find("\\includegraphics")
    end_idx = fixed.find("\\end{center}")
    assert begin_idx != -1 and begin_idx < fig_idx < end_idx
    # And contain a blank line between \vspace and {\small ...}.
    vspace_idx = fixed.find("\\vspace{0.3em}")
    text_idx = fixed.find("{\\small Caption", vspace_idx)
    between = fixed[vspace_idx:text_idx]
    assert "\n\n" in between, f"missing blank line between \\vspace and text: {between!r}"


def test_wraps_figure_in_begin_center_env() -> None:
    """The fix must wrap ``\\includegraphics`` in
    ``\\begin{center}...\\end{center}`` (an environment with explicit scope,
    not ``\\centering`` whose declaration leaks across the frame body)."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"  \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"  {\small Caption.}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    begin_idx = fixed.find("\\begin{center}")
    fig_idx = fixed.find("\\includegraphics")
    end_idx = fixed.find("\\end{center}")
    assert begin_idx != -1 and begin_idx < fig_idx < end_idx
    # \centering should NOT be present (we're using the env approach).
    assert "\\centering" not in fixed


def test_skips_wrapping_when_already_in_center_env() -> None:
    """If ``\\includegraphics`` is already inside ``\\begin{center}...\\end{center}``,
    don't double-wrap."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"  \begin{center}" "\n"
        r"    \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"  \end{center}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"" "\n"
        r"  {\small Caption.}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    assert fixed.count("\\begin{center}") == 1
    assert fixed.count("\\end{center}") == 1


def test_injects_blank_line_after_vspace_before_caption() -> None:
    """The fix must inject a blank line BETWEEN ``\\vspace`` and the next text."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"  \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"  {\small Caption.}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    vspace_idx = fixed.find("\\vspace{0.3em}")
    text_idx = fixed.find("{\\small Caption", vspace_idx)
    between = fixed[vspace_idx:text_idx]
    assert "\n\n" in between, f"missing blank line between \\vspace and text: {between!r}"


def test_idempotent_after_full_treatment() -> None:
    """Running the fixer twice on already-fixed tex produces the same result."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"  \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"  {\small Caption.}" "\n"
        r"\end{frame}" "\n"
    )
    once = enforce_figure_paragraph_break(tex)
    twice = enforce_figure_paragraph_break(once)
    assert once == twice


def test_idempotent_when_already_wrapped_with_blank_line() -> None:
    """Already-wrapped figure with blank line after → no change on re-run."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"  \begin{center}" "\n"
        r"    \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"  \end{center}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"" "\n"
        r"  {\small Caption text.}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    # Running it again must produce the same result (idempotent).
    assert enforce_figure_paragraph_break(fixed) == fixed


def test_no_injection_inside_columns_block() -> None:
    """When ``\\includegraphics`` is inside a ``\\begin{column}{...}``, the
    column layout is already a side-by-side flow; don't inject."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"\begin{columns}[T]" "\n"
        r"  \begin{column}{0.5\textwidth}" "\n"
        r"    \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"  \end{column}" "\n"
        r"  \begin{column}{0.5\textwidth}" "\n"
        r"    \begin{itemize}\item bullet\end{itemize}" "\n"
        r"  \end{column}" "\n"
        r"\end{columns}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    # No \begin{center} added — inside columns is a no-op zone.
    assert "\\begin{center}" not in fixed
    assert "\\centering" not in fixed
    assert fixed == tex


def test_no_injection_when_followed_by_end_frame() -> None:
    """``\\includegraphics`` immediately before ``\\end{frame}`` (no text) → no injection."""
    tex = (
        r"\begin{frame}{X}" "\n"
        r"  \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    assert fixed == tex


def test_handles_multiple_figures_in_one_deck() -> None:
    """Multi-frame deck with multiple ``\\includegraphics`` + text patterns: each gets wrapped."""
    tex = (
        r"\begin{frame}{A}" "\n"
        r"  \includegraphics[width=\linewidth]{p0-fig-001}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"  {\small Caption A.}" "\n"
        r"\end{frame}" "\n"
        r"\begin{frame}{B}" "\n"
        r"  \includegraphics[width=\linewidth]{p0-fig-002}" "\n"
        r"  \vspace{0.3em}" "\n"
        r"  {\small Caption B.}" "\n"
        r"\end{frame}" "\n"
    )
    fixed = enforce_figure_paragraph_break(tex)
    # Both frames should have a \begin{center}/\end{center} pair plus a blank
    # line between \vspace and {\small ...}.
    assert fixed.count("\\begin{center}") == 2
    assert fixed.count("\\end{center}") == 2
    assert fixed.count("\n\n") >= 2
