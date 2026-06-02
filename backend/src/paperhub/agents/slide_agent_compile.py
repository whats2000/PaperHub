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
    CompileCheckResult,
    KeyFigureBundle,
    PaperContextBundle,
)
from paperhub.pipelines.slide_pipeline.compile import compile_with_revise
from paperhub.pipelines.slide_pipeline.math_auditor import audit_math_frames
from paperhub.pipelines.slide_pipeline.overflow_detector import detect_overflow

Script = Literal["en", "cjk"]

_COMPILE_ERR_RE = re.compile(r"^!\s.+|^l\.\d+\s.+", re.MULTILINE)
_FRAME_COUNT_RE = re.compile(r"\\begin\{frame\}")


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
    # ok is not meaningful here — there's no compile pass — but we set it to
    # True iff the deterministic checks alone pass, for symmetry.
    ok = len(unrendered_math) == 0 and all(
        not s.exceeds_canvas_budget for s in frame_overflow
    )
    return CompileCheckResult(
        ok=ok,
        page_count=0,
        compile_errors=[],
        frame_overflow=frame_overflow,
        unrendered_math_frames=unrendered_math,
    )
