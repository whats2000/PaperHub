import pytest

from paperhub.agents.slide_agent_compile import (
    detect_bare_visuals,
    detect_decorated_blocks,
    detect_long_diagram_nodes,
    run_compile_check,
    run_density_check,
)
from paperhub.models.slide_domain import (
    FigureDimensions,
    KeyEquationBundle,
    KeyFigureBundle,
    PaperContextBundle,
)


def _bundle() -> PaperContextBundle:
    return PaperContextBundle(
        paper_id=1,
        paper_idx=0,
        title="t",
        authors=[],
        year=2025,
        narrative_summary="x",
        key_figures=[
            KeyFigureBundle(
                key="p0-fig-001",
                role="overview",
                one_line_interpretation="x",
                dimensions=FigureDimensions(width_px=600, height_px=900),
            )
        ],
        key_equations=[
            KeyEquationBundle(
                latex=r"\Phi = \sum a",
                role="visual_token_importance_score",
                notation_legend="",
            )
        ],
        section_excerpts=[],
        paper_newcommands=[],
    )


_GOOD_DECK = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{Intro}
\begin{itemize}\item short\end{itemize}
\end{frame}
\end{document}
"""

_MATH_TOPIC_NO_MATH_DECK = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{Visual Token Importance Scoring}
\begin{itemize}\item we score tokens\end{itemize}
\end{frame}
\end{document}
"""


@pytest.mark.asyncio
async def test_density_check_no_compile_runs_overflow_only(tmp_path):
    bundles = [_bundle()]
    result = await run_density_check(
        deck_tex=_GOOD_DECK,
        bundles=bundles,
        script="en",
    )
    # ok flag isn't meaningful for density_check (no pdflatex) — caller reads
    # frame_overflow + unrendered_math_frames directly.
    assert isinstance(result.frame_overflow, list)
    assert len(result.frame_overflow) == 1
    assert result.compile_errors == []
    assert result.page_count == 0   # density_check never runs pdflatex


# A block INSIDE a two-column layout (the breaking case) vs a block in a
# full-width frame (perfectly fine).
_BLOCK_DECK = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{Title}\titlepage\end{frame}
\begin{frame}{Two column block}
\begin{columns}
\begin{column}{0.5\textwidth}
\begin{block}{Formula}
\[ E = mc^2 \]
\end{block}
\end{column}
\begin{column}{0.5\textwidth}
right
\end{column}
\end{columns}
\end{frame}
\begin{frame}{Full-width block is fine}
\begin{block}{Definition}
\[ a^2 + b^2 = c^2 \]
\end{block}
\end{frame}
\end{document}
"""


def test_detect_decorated_blocks_flags_only_block_in_columns() -> None:
    signals = detect_decorated_blocks(_BLOCK_DECK)
    # Only the block INSIDE \begin{columns} is flagged; the full-width block is fine.
    assert len(signals) == 1
    assert signals[0].frame_index == 1            # 0-based over \begin{frame}
    assert signals[0].frame_title == "Two column block"
    assert "block" in signals[0].block_kinds


def test_detect_decorated_blocks_clean_deck() -> None:
    assert detect_decorated_blocks(_GOOD_DECK) == []


_DIAGRAM_DECK = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{Short labels are fine}
\smartdiagram[flow diagram:horizontal]{Unified Framework, Multi-Subspace, RoPE Case}
\end{frame}
\begin{frame}{Sentence in node}
\smartdiagram[descriptive diagram]{
  {Formalism, {Additive mechanisms (ALiBi) admit a strict group-theoretic formulation}},
  {Shared Laws, {Inherit exact relative distance laws of multiplicative methods}}
}
\end{frame}
\end{document}
"""


def test_detect_long_diagram_nodes_flags_only_sentence_labels() -> None:
    signals = detect_long_diagram_nodes(_DIAGRAM_DECK)
    # The short-label flow diagram is fine; only the sentence-packed one flags.
    assert len(signals) == 1
    assert signals[0].frame_index == 1
    assert signals[0].frame_title == "Sentence in node"
    assert signals[0].longest_label_chars > 50


def test_detect_long_diagram_nodes_clean_deck() -> None:
    assert detect_long_diagram_nodes(_GOOD_DECK) == []


_BARE_DECK = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{Bare figure}
\includegraphics[width=\linewidth]{p0-fig-001}
\end{frame}
\begin{frame}{Figure with caption is fine}
\includegraphics[width=\linewidth]{p0-fig-001}
\caption{The GRAPE architecture overview.}
\end{frame}
\begin{frame}{Figure with explanatory bullets is fine}
\includegraphics[width=0.6\linewidth]{p0-fig-001}
\begin{itemize}
\item The architecture maps positions to rotation matrices end to end.
\item It recovers RoPE as a constrained special case of the framework.
\end{itemize}
\end{frame}
\begin{frame}{Bare equation}
\[ E = mc^2 \]
\end{frame}
\begin{frame}{Equation with a legend is fine}
\[ \mathbf{G}(n) = \exp(n\,\omega\,\mathbf{L}) \]
where the generator rotates each token position by a learned per-head angle omega.
\end{frame}
\end{document}
"""


def test_detect_bare_visuals_flags_only_unexplained() -> None:
    sigs = detect_bare_visuals(_BARE_DECK)
    flagged = {(s.frame_index, s.kind) for s in sigs}
    # frame 0 (bare figure) and frame 3 (bare equation) are flagged; the
    # captioned, bulleted, and legend frames are fine.
    assert flagged == {(0, "figure"), (3, "equation")}


def test_detect_bare_visuals_clean_deck() -> None:
    # _GOOD_DECK has a single short-bullet frame, no standalone visual.
    assert detect_bare_visuals(_GOOD_DECK) == []


@pytest.mark.asyncio
async def test_density_check_surfaces_decorated_blocks() -> None:
    result = await run_density_check(deck_tex=_BLOCK_DECK, bundles=[_bundle()], script="en")
    assert len(result.decorated_blocks) == 1
    assert result.decorated_blocks[0].frame_title == "Two column block"
    assert result.ok is False  # a block-in-columns deck is not "clean"


@pytest.mark.asyncio
async def test_density_check_flags_math_topic_without_math():
    bundles = [_bundle()]
    result = await run_density_check(
        deck_tex=_MATH_TOPIC_NO_MATH_DECK,
        bundles=bundles,
        script="en",
    )
    assert len(result.unrendered_math_frames) == 1
    assert result.unrendered_math_frames[0].matched_equation_role == "visual_token_importance_score"


@pytest.mark.asyncio
async def test_compile_check_invokes_compile_with_revise_and_aggregates(tmp_path, monkeypatch):
    """compile_check writes deck.tex, runs pdflatex via compile.compile_with_revise,
    then computes overflow + math signals."""
    bundles = [_bundle()]

    async def fake_compile_with_revise(*, tex, workdir, tex_name, revise, max_retries):
        from paperhub.pipelines.slide_pipeline.compile import CompileResult
        return CompileResult(ok=True, attempts=1, tex=tex, log="all good", page_count=1)

    monkeypatch.setattr(
        "paperhub.agents.slide_agent_compile.compile_with_revise", fake_compile_with_revise
    )
    workdir = tmp_path / "slides"
    workdir.mkdir()
    figure_inventory = {b.key_figures[0].key: b.key_figures[0] for b in bundles}
    result = await run_compile_check(
        deck_tex=_GOOD_DECK,
        bundles=bundles,
        figure_inventory=figure_inventory,
        workdir=workdir,
        script="en",
    )
    assert result.ok is True
    assert result.page_count == 1
    assert result.compile_errors == []
    assert len(result.frame_overflow) == 1


@pytest.mark.asyncio
async def test_compile_check_records_compile_errors_when_not_ok(tmp_path, monkeypatch):
    async def fake_compile_with_revise(*, tex, workdir, tex_name, revise, max_retries):
        from paperhub.pipelines.slide_pipeline.compile import CompileResult
        return CompileResult(
            ok=False, attempts=4, tex=tex, log="! Undefined control sequence.\nl.5 \\foo",
            page_count=0,
        )

    monkeypatch.setattr(
        "paperhub.agents.slide_agent_compile.compile_with_revise", fake_compile_with_revise
    )
    result = await run_compile_check(
        deck_tex=_GOOD_DECK,
        bundles=[_bundle()],
        figure_inventory={},
        workdir=tmp_path,
        script="en",
    )
    assert result.ok is False
    assert any("Undefined control sequence" in e for e in result.compile_errors)


@pytest.mark.asyncio
async def test_compile_check_writes_additional_tex_from_bundle_newcommands(tmp_path, monkeypatch):
    """run_compile_check must drop an ADDITIONAL.tex into workdir aggregating
    paper_newcommands across all bundles, deduplicated, so the default
    preamble's \\input{ADDITIONAL.tex} doesn't error."""
    bundles = [
        PaperContextBundle(
            paper_id=1, paper_idx=0, title="t", authors=[], year=2025,
            narrative_summary="x", key_figures=[], key_equations=[],
            section_excerpts=[],
            paper_newcommands=[r"\newcommand{\R}{\mathbb{R}}", r"\newcommand{\bm}{...}"],
        ),
        PaperContextBundle(
            paper_id=2, paper_idx=1, title="u", authors=[], year=2025,
            narrative_summary="x", key_figures=[], key_equations=[],
            section_excerpts=[],
            paper_newcommands=[r"\newcommand{\R}{\mathbb{R}}", r"\newcommand{\K}{\mathbb{K}}"],
        ),
    ]

    async def fake_compile_with_revise(*, tex, workdir, tex_name, revise, max_retries):
        from paperhub.pipelines.slide_pipeline.compile import CompileResult
        return CompileResult(ok=True, attempts=1, tex=tex, log="", page_count=0)
    monkeypatch.setattr(
        "paperhub.agents.slide_agent_compile.compile_with_revise", fake_compile_with_revise
    )

    workdir = tmp_path / "slides"
    workdir.mkdir()
    await run_compile_check(
        deck_tex=r"\documentclass{beamer}\begin{document}\end{document}",
        bundles=bundles,
        figure_inventory={},
        workdir=workdir,
        script="en",
    )

    additional = (workdir / "ADDITIONAL.tex").read_text(encoding="utf-8")
    assert "\\newcommand{\\R}" in additional
    assert "\\newcommand{\\bm}" in additional
    assert "\\newcommand{\\K}" in additional
    # Deduplicated — \R appears once, not twice.
    assert additional.count("\\newcommand{\\R}") == 1


@pytest.mark.asyncio
async def test_compile_check_writes_empty_additional_tex_when_no_newcommands(tmp_path, monkeypatch):
    """No newcommands → empty ADDITIONAL.tex (still must exist so \\input doesn't crash)."""
    bundles = [
        PaperContextBundle(
            paper_id=1, paper_idx=0, title="t", authors=[], year=2025,
            narrative_summary="x", key_figures=[], key_equations=[],
            section_excerpts=[], paper_newcommands=[],
        ),
    ]
    async def fake_compile_with_revise(*, tex, workdir, tex_name, revise, max_retries):
        from paperhub.pipelines.slide_pipeline.compile import CompileResult
        return CompileResult(ok=True, attempts=1, tex=tex, log="", page_count=0)
    monkeypatch.setattr(
        "paperhub.agents.slide_agent_compile.compile_with_revise", fake_compile_with_revise
    )
    workdir = tmp_path / "slides"
    workdir.mkdir()
    await run_compile_check(
        deck_tex=r"\documentclass{beamer}\begin{document}\end{document}",
        bundles=bundles,
        figure_inventory={},
        workdir=workdir,
        script="en",
    )
    assert (workdir / "ADDITIONAL.tex").exists()
    # Content may be empty or just a newline — both fine.


@pytest.mark.asyncio
async def test_compile_check_surfaces_errors_even_when_compile_result_ok(tmp_path, monkeypatch):
    """F4.5 silent-recovery bug: pdflatex's -interaction=nonstopmode can recover
    from real errors and emit a partial PDF (ok=True via pdf_path.exists()) but
    the log shows them. compile_check MUST surface those errors so the agent
    re-iterates instead of treating the deck as clean."""
    async def fake_compile_with_revise(*, tex, workdir, tex_name, revise, max_retries):
        from paperhub.pipelines.slide_pipeline.compile import CompileResult
        return CompileResult(
            ok=True,  # pdf_path.exists() True even though deck is broken
            attempts=1,
            tex=tex,
            log=(
                "! Undefined control sequence.\n"
                "l.2 \\subtitle\n"
                "! LaTeX Error: Unicode character missing\n"
            ),
            page_count=1,
        )
    monkeypatch.setattr(
        "paperhub.agents.slide_agent_compile.compile_with_revise", fake_compile_with_revise
    )

    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()
    figure_inventory = {b.key_figures[0].key: b.key_figures[0] for b in bundles}

    result = await run_compile_check(
        deck_tex=_GOOD_DECK,
        bundles=bundles,
        figure_inventory=figure_inventory,
        workdir=workdir,
        script="en",
    )
    # Even though compile_result.ok was True, the error log MUST be surfaced.
    assert result.ok is False
    assert any("Undefined control sequence" in e for e in result.compile_errors)


@pytest.mark.asyncio
async def test_compile_check_flags_page_count_below_frame_count(tmp_path, monkeypatch):
    """F4.5: when pdflatex recovers from errors and produces 1 page from a
    5-frame deck, surface that as a synthetic compile error so the agent
    re-iterates even if the error-log parser missed the offending lines."""
    async def fake_compile_with_revise(*, tex, workdir, tex_name, revise, max_retries):
        from paperhub.pipelines.slide_pipeline.compile import CompileResult
        return CompileResult(ok=True, attempts=1, tex=tex, log="", page_count=1)
    monkeypatch.setattr(
        "paperhub.agents.slide_agent_compile.compile_with_revise", fake_compile_with_revise
    )

    bundles = [_bundle()]
    workdir = tmp_path / "slides"
    workdir.mkdir()

    deck_with_5_frames = (
        "\\documentclass{beamer}\n"
        "\\begin{document}\n"
        "\\begin{frame}{A}1\\end{frame}\n"
        "\\begin{frame}{B}2\\end{frame}\n"
        "\\begin{frame}{C}3\\end{frame}\n"
        "\\begin{frame}{D}4\\end{frame}\n"
        "\\begin{frame}{E}5\\end{frame}\n"
        "\\end{document}\n"
    )

    result = await run_compile_check(
        deck_tex=deck_with_5_frames,
        bundles=bundles,
        figure_inventory={},
        workdir=workdir,
        script="en",
    )
    assert result.ok is False
    assert any("page count" in e.lower() for e in result.compile_errors)


@pytest.mark.asyncio
async def test_compile_check_ok_flag_false_when_math_contract_violated(tmp_path, monkeypatch):
    async def fake_compile_with_revise(*, tex, workdir, tex_name, revise, max_retries):
        from paperhub.pipelines.slide_pipeline.compile import CompileResult
        return CompileResult(ok=True, attempts=1, tex=tex, log="", page_count=1)

    monkeypatch.setattr(
        "paperhub.agents.slide_agent_compile.compile_with_revise", fake_compile_with_revise
    )
    result = await run_compile_check(
        deck_tex=_MATH_TOPIC_NO_MATH_DECK,
        bundles=[_bundle()],
        figure_inventory={},
        workdir=tmp_path,
        script="en",
    )
    # compile succeeded BUT math contract violated → ok=False (gates done()).
    assert result.ok is False
    assert len(result.unrendered_math_frames) == 1
    assert result.compile_errors == []
