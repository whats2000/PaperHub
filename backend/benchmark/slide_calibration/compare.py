"""Inspect round-N artifacts: dump objective machine-checks, open PDFs for human review.

Usage:
    uv run python -m benchmark.slide_calibration.compare --round 0

This harness drives slide-generation runs and dumps artifacts (slides.tex,
slides.pdf, tool_calls.json) for the developer to review. It does NOT
score against any reference deck — style and presentability are human
calls, not a pattern-match against an example. The example deck at
`D:\\GitHub\\Final_Report` is ONE illustration of a professional output;
the system's job is to be capable of professional output in general,
not to reproduce that specific surface form.

What this script does:
- Walk the round's artifact directories.
- For each generated `slides.tex`, dump objective metrics into
  `_checks.json`: frame env count, figure-include count, presence of the
  T8 paper_newcommands marker, detected document language hint.
- Open the generated PDF for the developer's visual review.

What this script does NOT do (deliberately):
- Compare against `D:\\GitHub\\Final_Report`'s `slides.pdf`.
- Write any scorecard or pass/fail dimension rubric.
- Open the example deck side-by-side. If the developer wants that, they
  open the example manually.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from ._common import DEV_PAPERS, RESULTS_DIR


def open_pdf(path: Path) -> None:
    if not path.exists():
        print(f"[compare] PDF not found: {path}")
        return
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(path))  # noqa: S606
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=True)
        else:
            subprocess.run(["xdg-open", str(path)], check=True)
    except Exception as e:
        print(f"[compare] could not open {path}: {e}")


def objective_checks(round_dir: Path) -> dict:
    """Machine-checkable metrics only. No style scoring."""
    out: dict = {"round_dir": str(round_dir)}
    tex_path = round_dir / "slides.tex"
    if not tex_path.exists():
        out["status"] = "no_tex_artifact"
        return out

    tex = tex_path.read_text(encoding="utf-8", errors="ignore")
    out["frame_envs"] = len(re.findall(r"\\begin\{frame\}", tex))
    out["includegraphics_count"] = len(
        re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}", tex)
    )
    out["paper_newcommands_block_present"] = (
        "% BEGIN paperhub:paper_newcommands" in tex
    )
    out["xelatex_magic_present"] = tex.lstrip().startswith("% !TeX program = xelatex")
    out["theme_line"] = next(
        (m.group(0) for m in re.finditer(r"\\usetheme\{[^}]+\}", tex)),
        None,
    )

    pdf_path = round_dir / "slides.pdf"
    if pdf_path.exists():
        out["pdf_present"] = True
        out["pdf_bytes"] = pdf_path.stat().st_size
    else:
        out["pdf_present"] = False

    return out


def compare_round(round_no: int) -> int:
    round_root = RESULTS_DIR / f"round_{round_no:02d}"
    if not round_root.exists():
        raise SystemExit(f"no artifacts for round {round_no}: {round_root} missing")

    for arxiv_id in DEV_PAPERS:
        a_dir = round_root / "a" / arxiv_id
        if not a_dir.exists():
            print(f"[compare] skip a/{arxiv_id} — no run yet")
            continue
        checks = objective_checks(a_dir)
        (a_dir / "_checks.json").write_text(
            json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n[compare-a:{arxiv_id}] checks: {json.dumps(checks, ensure_ascii=False)}")
        open_pdf(a_dir / "slides.pdf")

    for lang in ["en", "zh"]:
        b_dir = round_root / "b" / lang
        if not b_dir.exists():
            print(f"[compare] skip b/{lang} — no run yet")
            continue
        checks = objective_checks(b_dir)
        (b_dir / "_checks.json").write_text(
            json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n[compare-b:{lang}] checks: {json.dumps(checks, ensure_ascii=False)}")
        open_pdf(b_dir / "slides.pdf")

    print(
        f"\n[compare] done. Open each PaperHub PDF and review for yourself; "
        "style is your judgement, not a pattern-match against any example."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Dump objective metrics + open generated PDFs for human review."
    )
    ap.add_argument("--round", dest="round_no", type=int, required=True)
    args = ap.parse_args()
    return compare_round(args.round_no)


if __name__ == "__main__":
    sys.exit(main())
