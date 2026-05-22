# backend/src/paperhub/pipelines/slide_pipeline/compile.py
"""Beamer compile-with-revise loop.

Skeleton adapted from reference/paper2slides-plus/src/compiler.py @ 88515c4
(MIT); the LLM-revise step is injected as a callback so this module stays
adapter-agnostic (the Report Agent passes a closure over the LlmAdapter).
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from paperhub.pipelines.slide_pipeline.latex_helpers import sanitize_frametitles

ReviseFn = Callable[[str, str], Awaitable[str]]  # (pdflatex_log, current_tex) -> fixed_tex

PDFLATEX = shutil.which("pdflatex") or "pdflatex"


@dataclass
class CompileResult:
    ok: bool
    attempts: int
    tex: str
    log: str
    page_count: int


def _run_pdflatex(tex_name: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    cmd = [PDFLATEX, "-interaction=nonstopmode", tex_name]
    return subprocess.run(  # noqa: S603
        cmd, cwd=str(workdir), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )


def _page_count(pdf: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except Exception:
        return 0


async def compile_with_revise(
    *, tex: str, workdir: Path, tex_name: str, revise: ReviseFn, max_retries: int = 3,
) -> CompileResult:
    workdir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    current = sanitize_frametitles(tex)
    last_log = ""
    pdf_path = workdir / Path(tex_name).with_suffix(".pdf").name
    for attempt in range(1, max_retries + 2):
        (workdir / tex_name).write_text(current, encoding="utf-8")
        if pdf_path.exists():
            pdf_path.unlink()
        try:
            proc = _run_pdflatex(tex_name, workdir)
            last_log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            last_log = f"pdflatex timed out: {exc}"
            proc = subprocess.CompletedProcess([PDFLATEX], 1, "", last_log)
        if proc.returncode == 0 or pdf_path.exists():
            return CompileResult(True, attempt, current, last_log, _page_count(pdf_path))
        if attempt > max_retries:
            break
        current = sanitize_frametitles(await revise(last_log[-4000:], current))
    return CompileResult(False, max_retries + 1, current, last_log, 0)
