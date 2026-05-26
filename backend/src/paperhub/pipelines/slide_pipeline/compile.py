# backend/src/paperhub/pipelines/slide_pipeline/compile.py
"""Beamer compile-with-revise loop.

Skeleton adapted from reference/paper2slides-plus/src/compiler.py @ 88515c4
(MIT); the LLM-revise step is injected as a callback so this module stays
adapter-agnostic (the Report Agent passes a closure over the LlmAdapter).
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from paperhub.pipelines.slide_pipeline.latex_helpers import sanitize_frametitles

_LOG = logging.getLogger(__name__)

ReviseFn = Callable[[str, str], Awaitable[str]]  # (pdflatex_log, current_tex) -> fixed_tex

PDFLATEX = shutil.which("pdflatex") or "pdflatex"

_OVERFULL_VBOX_RE = re.compile(r"Overfull \\vbox")

# A deck that uses any of these MUST be compiled with xelatex (pdflatex cannot
# run them): the Unicode-engine packages xeCJK/fontspec/ctex, or an explicit
# ``% !TeX program = xelatex`` magic comment the LLM emits for CJK decks.
_XELATEX_TRIGGERS = ("xecjk", "fontspec", "ctex", "% !tex program = xelatex")

_XECJK_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{xeCJK\}", re.IGNORECASE)
# Default CJK font shipped by the image's fonts-noto-cjk package. Covers
# Simplified + Traditional + Japanese + Korean glyphs (Noto CJK is unified).
_DEFAULT_CJK_FONT = "Noto Serif CJK SC"


def _has_overfull_vbox(log: str) -> bool:
    """Return True when the pdflatex log contains an Overfull \\vbox warning."""
    return bool(_OVERFULL_VBOX_RE.search(log))


def select_engine(tex: str) -> str:
    """Pick the LaTeX engine the deck requires.

    Returns the resolved ``xelatex`` path when the source declares a
    Unicode-engine dependency (xeCJK / fontspec / ctex, or an explicit
    ``% !TeX program = xelatex`` magic comment) AND xelatex is actually
    installed; otherwise ``pdflatex``. The xelatex requirement is real — a CJK
    (e.g. Chinese) deck built for xeCJK silently drops every CJK glyph under
    pdflatex — so we honour it instead of hardcoding pdflatex.
    """
    low = tex.lower()
    if any(trigger in low for trigger in _XELATEX_TRIGGERS):
        xelatex = shutil.which("xelatex")
        if xelatex:
            return xelatex
        _LOG.warning(
            "deck requires xelatex (xeCJK/fontspec/ctex) but xelatex is not on "
            "PATH; falling back to pdflatex — CJK/Unicode glyphs may not render"
        )
    return shutil.which("pdflatex") or PDFLATEX


def ensure_cjk_font(tex: str, font: str = _DEFAULT_CJK_FONT) -> str:
    """Inject a default ``\\setCJKmainfont`` when xeCJK is used but unset.

    xeCJK has no built-in default CJK font on Linux TeX Live, so a preamble
    with a bare ``\\usepackage{xeCJK}`` and no ``\\setCJKmainfont`` errors out
    (MiKTeX on Windows masks this with a configured default). The LLM commonly
    emits exactly that, so we add a font that the image provides. No-op when
    xeCJK is absent or a CJK main font is already set.
    """
    match = _XECJK_RE.search(tex)
    if match is None or "\\setCJKmainfont" in tex:
        return tex
    insert = f"\n\\setCJKmainfont{{{font}}}"
    return tex[: match.end()] + insert + tex[match.end() :]


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    attempts: int
    tex: str
    log: str
    page_count: int


def _run_latex(engine: str, tex_name: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    cmd = [engine, "-interaction=nonstopmode", tex_name]
    return subprocess.run(  # noqa: S603 — engine resolved via shutil.which, sandboxed workdir
        cmd, cwd=str(workdir), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )


def _page_count(pdf: Path) -> int:
    try:
        return len(PdfReader(str(pdf)).pages)
    except Exception as exc:  # noqa: BLE001 — page count is best-effort metadata
        _LOG.warning("_page_count failed for %s: %r", pdf, exc)
        return 0


async def compile_with_revise(
    *, tex: str, workdir: Path, tex_name: str, revise: ReviseFn, max_retries: int = 3,
) -> CompileResult:
    # All blocking I/O (pdflatex, file writes, pypdf) is pushed to a worker
    # thread so a multi-second compile never stalls the FastAPI event loop.
    # The async ``revise`` callback stays on the loop (it awaits the LLM).
    await asyncio.to_thread(workdir.mkdir, parents=True, exist_ok=True)
    current = ensure_cjk_font(sanitize_frametitles(tex))
    last_log = ""
    pdf_path = workdir / Path(tex_name).with_suffix(".pdf").name
    for attempt in range(1, max_retries + 2):
        # Re-derive engine + font each attempt: the revise step rewrites the
        # TeX and could change (or drop) the xeCJK/font lines.
        current = ensure_cjk_font(current)
        engine = select_engine(current)
        await asyncio.to_thread((workdir / tex_name).write_text, current, encoding="utf-8")
        if pdf_path.exists():
            await asyncio.to_thread(pdf_path.unlink)
        try:
            proc = await asyncio.to_thread(_run_latex, engine, tex_name, workdir)
            last_log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            last_log = f"{engine} timed out: {exc}"
            proc = subprocess.CompletedProcess([engine], 1, "", last_log)
        pdf_produced = proc.returncode == 0 or pdf_path.exists()
        if pdf_produced and not _has_overfull_vbox(last_log):
            pages = await asyncio.to_thread(_page_count, pdf_path)
            return CompileResult(True, attempt, current, last_log, pages)
        if attempt <= max_retries:
            # Either a hard compile error OR a clean-exit-but-Overfull run —
            # either way, ask the LLM to tighten/fix the TeX and retry.
            current = sanitize_frametitles(await revise(last_log[-4000:], current))
        elif pdf_produced:
            # Retries exhausted but a PDF exists (overfull every time).
            # Return ok=True — a degraded deck is better than a lost deck.
            pages = await asyncio.to_thread(_page_count, pdf_path)
            _LOG.warning(
                "compile_with_revise: Overfull \\vbox persists after %d attempts; "
                "emitting degraded PDF (%d pages)",
                max_retries + 1,
                pages,
            )
            return CompileResult(True, max_retries + 1, current, last_log, pages)
    return CompileResult(False, max_retries + 1, current, last_log, 0)
