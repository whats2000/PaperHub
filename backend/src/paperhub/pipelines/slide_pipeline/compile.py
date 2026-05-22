# backend/src/paperhub/pipelines/slide_pipeline/compile.py
"""Beamer compile-with-revise loop.

Skeleton adapted from reference/paper2slides-plus/src/compiler.py @ 88515c4
(MIT); the LLM-revise step is injected as a callback so this module stays
adapter-agnostic (the Report Agent passes a closure over the LlmAdapter).
"""
from __future__ import annotations

import asyncio
import logging
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


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    attempts: int
    tex: str
    log: str
    page_count: int


def _run_pdflatex(tex_name: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    cmd = [PDFLATEX, "-interaction=nonstopmode", tex_name]
    return subprocess.run(  # noqa: S603 — fixed binary, sandboxed workdir
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
    current = sanitize_frametitles(tex)
    last_log = ""
    pdf_path = workdir / Path(tex_name).with_suffix(".pdf").name
    for attempt in range(1, max_retries + 2):
        await asyncio.to_thread((workdir / tex_name).write_text, current, encoding="utf-8")
        if pdf_path.exists():
            await asyncio.to_thread(pdf_path.unlink)
        try:
            proc = await asyncio.to_thread(_run_pdflatex, tex_name, workdir)
            last_log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            last_log = f"pdflatex timed out: {exc}"
            proc = subprocess.CompletedProcess([PDFLATEX], 1, "", last_log)
        if proc.returncode == 0 or pdf_path.exists():
            pages = await asyncio.to_thread(_page_count, pdf_path)
            return CompileResult(True, attempt, current, last_log, pages)
        if attempt <= max_retries:
            current = sanitize_frametitles(await revise(last_log[-4000:], current))
    return CompileResult(False, max_retries + 1, current, last_log, 0)
