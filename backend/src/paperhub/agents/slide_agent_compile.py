"""F4.5 compile_check + density_check tool implementations.

compile_check: write deck.tex → pdflatex (via the surviving compile_with_revise
                 with max_retries=0 — we want pure compile, not a revise loop,
                 because the agent IS the revise loop) → run overflow_detector +
                 math_auditor → aggregate into a CompileCheckResult.
density_check: same minus pdflatex (speculative-edit verification).

ok flag: True iff zero compile_errors AND zero unrendered_math_frames.
         (Frame overflow is advisory — does NOT zero the ok flag, but the
          agent prompt tells the LLM to act on overage_tokens > 0 until budget
          exhaustion.)
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Literal

from paperhub.agents._canvas_budget import load_canvas_budget
from paperhub.models.slide_domain import (
    BareVisualSignal,
    CompileCheckResult,
    DecoratedBlockSignal,
    KeyFigureBundle,
    LongDiagramNodeSignal,
    PaperContextBundle,
)
from paperhub.pipelines.slide_pipeline.compile import compile_with_revise
from paperhub.pipelines.slide_pipeline.math_auditor import audit_math_frames
from paperhub.pipelines.slide_pipeline.overflow_detector import detect_overflow

Script = Literal["en", "cjk"]

_COMPILE_ERR_RE = re.compile(r"^!\s.+|^l\.\d+\s.+", re.MULTILINE)
_FRAME_COUNT_RE = re.compile(r"\\begin\{frame\}")

# Decorated-box lint: a beamer ``block`` / ``exampleblock`` / ``alertblock``
# titled box is FINE in a full-width frame, but INSIDE a two-column
# (``\begin{columns}``) layout it overflows the narrow column and breaks the
# slide (live run 569: a block-wrapped equation beside a figure). Only blocks
# nested in a columns environment are flagged.
_FRAME_SPAN_RE = re.compile(r"\\begin\{frame\}.*?\\end\{frame\}", re.DOTALL)
_COLUMNS_SPAN_RE = re.compile(r"\\begin\{columns\}.*?\\end\{columns\}", re.DOTALL)
_BLOCK_RE = re.compile(r"\\begin\{(block|exampleblock|alertblock)\}")
_FRAMETITLE_RE = re.compile(r"\\begin\{frame\}\s*(?:\[[^\]]*\])?\s*\{(.*?)\}", re.DOTALL)


def _frame_title(frame_tex: str) -> str:
    m = _FRAMETITLE_RE.search(frame_tex)
    return (m.group(1).strip() if m else "")[:80]


def detect_decorated_blocks(deck_tex: str) -> list[DecoratedBlockSignal]:
    """Flag frames that put a decorated box INSIDE a two-column layout."""
    signals: list[DecoratedBlockSignal] = []
    for idx, m in enumerate(_FRAME_SPAN_RE.finditer(deck_tex)):
        frame = m.group(0)
        kinds: set[str] = set()
        for cols in _COLUMNS_SPAN_RE.finditer(frame):
            kinds.update(_BLOCK_RE.findall(cols.group(0)))
        if kinds:
            signals.append(
                DecoratedBlockSignal(
                    frame_index=idx,
                    frame_title=_frame_title(frame),
                    block_kinds=sorted(kinds),
                )
            )
    return signals


# Long-diagram-node lint: smartdiagram sizes a node to its label, so a
# sentence-length label overflows into a giant overlapping bubble (live run 570
# slide 6). A label that is a short noun phrase is fine; one over this many chars
# is a sentence that belongs in bullets beside the diagram, not in the node.
_SMARTDIAGRAM_RE = re.compile(r"\\smartdiagram(?:\[[^\]]*\])?\s*\{")
_MAX_DIAGRAM_LABEL_CHARS = 50


def _smartdiagram_bodies(frame_tex: str) -> list[str]:
    """Return the brace-matched body of each \\smartdiagram in the frame."""
    bodies: list[str] = []
    for m in _SMARTDIAGRAM_RE.finditer(frame_tex):
        depth = 1
        i = m.end()
        start = i
        while i < len(frame_tex) and depth:
            ch = frame_tex[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        bodies.append(frame_tex[start : i - 1])
    return bodies


def detect_long_diagram_nodes(deck_tex: str) -> list[LongDiagramNodeSignal]:
    """Flag frames whose \\smartdiagram has a sentence-length node label."""
    signals: list[LongDiagramNodeSignal] = []
    for idx, m in enumerate(_FRAME_SPAN_RE.finditer(deck_tex)):
        frame = m.group(0)
        longest = ""
        for body in _smartdiagram_bodies(frame):
            # node labels are the text tokens between braces/commas
            for tok in re.split(r"[{},]", body):
                tok = " ".join(tok.split())
                if len(tok) > len(longest):
                    longest = tok
        if len(longest) > _MAX_DIAGRAM_LABEL_CHARS:
            signals.append(
                LongDiagramNodeSignal(
                    frame_index=idx,
                    frame_title=_frame_title(frame),
                    longest_label_chars=len(longest),
                    sample_label=longest[:120],
                )
            )
    return signals


# Bare-visual lint: a figure / table / equation presented with NO caption and
# essentially NO explanatory text leaves the audience unsure what it means. A
# visual WITH explanatory side text is fine. Conservative threshold (only flag a
# visual that is truly alone) so a figure+bullets slide is never flagged.
_MIN_EXPLAIN_WORDS = 8
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\s*\{[^}]*\}")
_TABULAR_ENV_RE = re.compile(r"\\begin\{tabular\}.*?\\end\{tabular\}", re.DOTALL)
_MATH_ENV_RE = re.compile(
    r"\\\[.*?\\\]|\\begin\{(?:equation|align|align\*|gather|gather\*|equation\*)\}.*?"
    r"\\end\{(?:equation|align|align\*|gather|gather\*|equation\*)\}",
    re.DOTALL,
)
_CAPTION_RE = re.compile(r"\\caption\s*\{")
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?")


def _explanatory_words(frame_tex: str) -> int:
    """Count plain-prose words left after removing the visual + LaTeX scaffolding.

    Strips frame title, every visual environment (figure/table/math/diagram), and
    LaTeX commands/braces; what remains is the explanatory text the audience can
    read. Inline math ($...$) inside a sentence is kept (it is part of prose).
    """
    s = frame_tex
    s = re.sub(r"\\begin\{frame\}\s*(?:\[[^\]]*\])?\s*\{.*?\}", " ", s, count=1, flags=re.DOTALL)
    s = re.sub(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", " ", s, flags=re.DOTALL)
    s = _TABULAR_ENV_RE.sub(" ", s)
    s = _MATH_ENV_RE.sub(" ", s)
    for body in _smartdiagram_bodies(s):
        s = s.replace(body, " ")
    s = _SMARTDIAGRAM_RE.sub(" ", s)
    s = _INCLUDEGRAPHICS_RE.sub(" ", s)
    s = _LATEX_CMD_RE.sub(" ", s)  # drop remaining commands (\item, \textbf, ...)
    s = re.sub(r"[{}$&\\]", " ", s)
    # words = alphabetic tokens of length >= 2 (ignores stray symbols / single letters)
    return len([w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", s) if len(w) >= 2])


def detect_bare_visuals(deck_tex: str) -> list[BareVisualSignal]:
    """Flag frames whose figure/table/equation has no caption AND no explanation."""
    signals: list[BareVisualSignal] = []
    for idx, m in enumerate(_FRAME_SPAN_RE.finditer(deck_tex)):
        frame = m.group(0)
        if _CAPTION_RE.search(frame):
            continue  # a caption already explains the visual
        if _INCLUDEGRAPHICS_RE.search(frame):
            kind = "figure"
        elif _TABULAR_ENV_RE.search(frame):
            kind = "table"
        elif _MATH_ENV_RE.search(frame):
            kind = "equation"
        else:
            continue  # no standalone visual element on this frame
        words = _explanatory_words(frame)
        if words < _MIN_EXPLAIN_WORDS:
            signals.append(
                BareVisualSignal(
                    frame_index=idx,
                    frame_title=_frame_title(frame),
                    kind=kind,
                    explain_words=words,
                )
            )
    return signals


def _parse_compile_errors(log: str) -> list[str]:
    """Extract pdflatex error lines (best-effort, lossy)."""
    return [m.group(0).strip() for m in _COMPILE_ERR_RE.finditer(log)][:20]


async def _noop_revise(log: str, tex: str) -> str:
    """compile_check disables compile_with_revise's internal revise loop —
    the slide_agent IS the revise loop via its tool calls."""
    return tex


async def run_compile_check(
    *,
    deck_tex: str,
    bundles: list[PaperContextBundle],
    figure_inventory: dict[str, KeyFigureBundle],
    workdir: Path,
    script: Script = "en",
    tex_name: str = "deck.tex",
) -> CompileCheckResult:
    """Write deck.tex, compile once, run detectors, aggregate."""
    # F4.5 v2.25: write an ADDITIONAL.tex containing aggregated paper
    # newcommands so the default preamble's ``\input{ADDITIONAL.tex}`` doesn't
    # fail with ``! LaTeX Error: File `ADDITIONAL.tex' not found.`` (real-API
    # benchmark Run 342-346 burned ~3 tool calls per case on this preventable
    # error before this fix).
    await asyncio.to_thread(workdir.mkdir, parents=True, exist_ok=True)
    macros: list[str] = []
    seen: set[str] = set()
    for b in bundles:
        for raw in b.paper_newcommands or []:
            line = raw.strip()
            if line and line not in seen:
                macros.append(line)
                seen.add(line)
    additional_tex = "\n".join(macros) + ("\n" if macros else "")
    # asyncio.to_thread keeps the event loop free during file IO (matches the
    # pattern elsewhere in this module).
    await asyncio.to_thread(
        (workdir / "ADDITIONAL.tex").write_text, additional_tex, encoding="utf-8"
    )

    compile_result = await compile_with_revise(
        tex=deck_tex,
        workdir=workdir,
        tex_name=tex_name,
        revise=_noop_revise,
        max_retries=0,
    )
    # F4.5: ALWAYS parse compile errors regardless of compile_result.ok.
    # pdflatex in -interaction=nonstopmode can recover from real errors and
    # produce a partial PDF, which makes compile_result.ok=True (because
    # pdf_path.exists()) even though the deck is broken. We need to surface
    # the errors so the agent knows to fix them. Real-API benchmark seventh
    # round (run 362, slides-multi-zh) caught this: a deck with no preamble
    # compiled into a broken 1-page PDF, errors in the log were silenced,
    # and the agent called done() believing all contracts were clean.
    compile_errors = _parse_compile_errors(compile_result.log)

    canvas_budget = load_canvas_budget()
    frame_overflow = detect_overflow(
        deck_tex=deck_tex,
        figure_inventory=figure_inventory,
        canvas_budget=canvas_budget,
        pdflatex_log=compile_result.log,
        script=script,
    )
    unrendered_math = audit_math_frames(deck_tex=deck_tex, bundles=bundles)

    # F4.5: defensive — if pdflatex produced fewer pages than the deck has
    # frames, pdflatex likely encountered errors and went into recovery mode.
    # Surface this as a synthetic compile error so the agent re-iterates
    # even when the error-log parser missed the offending lines.
    expected_frames = len(_FRAME_COUNT_RE.findall(deck_tex))
    actual_pages = compile_result.page_count
    # Allow page_count == expected (normal) OR expected + 1 (e.g. \maketitle
    # adds a page). Reject when actual_pages < expected_frames - 1.
    if expected_frames > 0 and actual_pages < expected_frames - 1:
        compile_errors.append(
            f"PDF page count ({actual_pages}) is less than deck frame count "
            f"({expected_frames}) — pdflatex likely recovered from errors. "
            f"Check the log above; common causes: missing \\documentclass, "
            f"missing \\usepackage, unbalanced braces, undefined commands."
        )

    # F4.5: ok = True iff zero compile errors AND zero unrendered math frames.
    # We DO NOT trust compile_result.ok alone — pdflatex's error-recovery can
    # produce a partial PDF that passes pdf_path.exists() with broken content.
    # The compile_errors length check above (now always populated) catches it.
    ok = len(compile_errors) == 0 and len(unrendered_math) == 0
    return CompileCheckResult(
        ok=ok,
        page_count=compile_result.page_count,
        compile_errors=compile_errors,
        frame_overflow=frame_overflow,
        unrendered_math_frames=unrendered_math,
        decorated_blocks=detect_decorated_blocks(deck_tex),
        long_diagram_nodes=detect_long_diagram_nodes(deck_tex),
        bare_visuals=detect_bare_visuals(deck_tex),
    )


async def run_density_check(
    *,
    deck_tex: str,
    bundles: list[PaperContextBundle],
    script: Script = "en",
    figure_inventory: dict[str, KeyFigureBundle] | None = None,
) -> CompileCheckResult:
    """Run overflow + math detectors WITHOUT pdflatex (speculative-edit verify).

    Returns a CompileCheckResult with page_count=0 and compile_errors=[] — the
    agent reads frame_overflow + unrendered_math_frames to decide whether a
    speculative split / replace would land within budget before paying the
    compile cost.
    """
    canvas_budget = load_canvas_budget()
    inv = figure_inventory or {}
    frame_overflow = detect_overflow(
        deck_tex=deck_tex,
        figure_inventory=inv,
        canvas_budget=canvas_budget,
        pdflatex_log="",
        script=script,
    )
    unrendered_math = audit_math_frames(deck_tex=deck_tex, bundles=bundles)
    decorated_blocks = detect_decorated_blocks(deck_tex)
    long_diagram_nodes = detect_long_diagram_nodes(deck_tex)
    bare_visuals = detect_bare_visuals(deck_tex)
    # ok is not meaningful here — there's no compile pass — but we set it to
    # True iff the deterministic checks alone pass, for symmetry.
    ok = (
        len(unrendered_math) == 0
        and len(decorated_blocks) == 0
        and len(long_diagram_nodes) == 0
        and len(bare_visuals) == 0
        and all(not s.exceeds_canvas_budget for s in frame_overflow)
    )
    return CompileCheckResult(
        ok=ok,
        page_count=0,
        compile_errors=[],
        frame_overflow=frame_overflow,
        unrendered_math_frames=unrendered_math,
        decorated_blocks=decorated_blocks,
        long_diagram_nodes=long_diagram_nodes,
        bare_visuals=bare_visuals,
    )
