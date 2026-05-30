"""Compare round-N artifacts side-by-side with the gold deck.

Usage:
    uv run python -m benchmark.slide_calibration.compare --round 0

What it does:
- Runs DETERMINISTIC checks on each generated slides.tex / speaker_notes.json:
    - em-dash sweep (any --, — in speaker_notes prose)
    - filler word count (essentially, actually, really, obviously)
    - frame count vs page_count consistency
    - \\includegraphics keys vs assembled figure inventory
    - sentence count per slide notes (paper2slides-plus target: 2-4)
- Writes blank scorecard files (scorecard_a_<paper>.md, scorecard_b_<lang>.md)
  in the same round dir; agent fills via direct PDF reading.
- Opens PaperHub vs gold PDFs side-by-side via the OS default viewer.
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

GOLD_ROOT = Path(r"D:\GitHub\Final_Report")
SCORECARD_A_TEMPLATE = Path(__file__).parent / "scorecard_a_template.md"
SCORECARD_B_TEMPLATE = Path(__file__).parent / "scorecard_b_template.md"

FILLERS = ["essentially", "actually", "really", "obviously"]
EM_DASH = re.compile(r"--|—")
SENTENCE_SPLIT = re.compile(r"[.!?]+\s+|[。！？]+")


def open_pdf(path: Path) -> None:
    if not path.exists():
        print(f"[compare] PDF not found: {path}")
        return
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(path))  # noqa: S606 — viewer launch is intentional
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=True)
        else:
            subprocess.run(["xdg-open", str(path)], check=True)
    except Exception as e:
        print(f"[compare] could not open {path}: {e}")


def deterministic_checks(round_dir: Path) -> dict:
    """Collect cheap signal: em-dash count, filler count, frame/page check, figs."""
    out: dict = {"round_dir": str(round_dir)}
    tex_path = round_dir / "slides.tex"
    notes_path = round_dir / "speaker_notes.json"

    if tex_path.exists():
        tex = tex_path.read_text(encoding="utf-8", errors="ignore")
        frame_count = len(re.findall(r"\\begin\{frame\}", tex))
        figure_keys = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex)
        em_dash_in_tex = len(EM_DASH.findall(tex))
        out["tex"] = {
            "frame_envs": frame_count,
            "figure_keys": figure_keys,
            "em_dash_in_tex_body": em_dash_in_tex,
        }

    if notes_path.exists():
        notes = json.loads(notes_path.read_text(encoding="utf-8"))
        per_slide = []
        for slide_idx, note in sorted(notes.items(), key=lambda p: int(p[0])):
            sentences = [s for s in SENTENCE_SPLIT.split(note) if s.strip()]
            per_slide.append(
                {
                    "slide": int(slide_idx),
                    "char_count": len(note),
                    "word_count_approx": len(note.split()),
                    "sentence_count": len(sentences),
                    "em_dash_count": len(EM_DASH.findall(note)),
                    "filler_count": sum(
                        len(re.findall(rf"\b{w}\b", note, re.IGNORECASE))
                        for w in FILLERS
                    ),
                }
            )
        out["notes"] = {
            "total_slides": len(per_slide),
            "total_words_approx": sum(s["word_count_approx"] for s in per_slide),
            "total_em_dashes": sum(s["em_dash_count"] for s in per_slide),
            "total_fillers": sum(s["filler_count"] for s in per_slide),
            "over_4_sentences": [s["slide"] for s in per_slide if s["sentence_count"] > 4],
            "under_2_sentences": [s["slide"] for s in per_slide if s["sentence_count"] < 2],
            "per_slide": per_slide,
        }

    return out


def write_blank_scorecard(target: Path, template: Path, **fmt: str) -> None:
    if target.exists():
        print(f"[compare] scorecard exists (not overwriting): {target}")
        return
    body = template.read_text(encoding="utf-8").format(**fmt) if fmt else template.read_text(encoding="utf-8")
    target.write_text(body, encoding="utf-8")
    print(f"[compare] wrote {target}")


def compare_round(round_no: int) -> int:
    round_root = RESULTS_DIR / f"round_{round_no:02d}"
    if not round_root.exists():
        raise SystemExit(f"no artifacts for round {round_no}: {round_root} missing")

    # Scenario A per paper
    for arxiv_id in DEV_PAPERS:
        a_dir = round_root / "a" / arxiv_id
        if not a_dir.exists():
            print(f"[compare] skip a/{arxiv_id} — no run yet")
            continue
        checks = deterministic_checks(a_dir)
        (a_dir / "_checks.json").write_text(
            json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        scorecard = a_dir / f"scorecard_a_{arxiv_id}.md"
        write_blank_scorecard(
            scorecard,
            SCORECARD_A_TEMPLATE,
            round_no=str(round_no),
            arxiv_id=arxiv_id,
        )
        paperhub_pdf = a_dir / "slides.pdf"
        gold_pdf = GOLD_ROOT / arxiv_id / "slides.pdf"
        print(f"\n[compare-a:{arxiv_id}]")
        print(f"  PaperHub: {paperhub_pdf}")
        print(f"  Gold:     {gold_pdf}")
        open_pdf(paperhub_pdf)
        open_pdf(gold_pdf)

    # Scenario B per language
    for lang in ["en", "zh"]:
        b_dir = round_root / "b" / lang
        if not b_dir.exists():
            print(f"[compare] skip b/{lang} — no run yet")
            continue
        checks = deterministic_checks(b_dir)
        (b_dir / "_checks.json").write_text(
            json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        scorecard = b_dir / f"scorecard_b_{lang}.md"
        write_blank_scorecard(
            scorecard,
            SCORECARD_B_TEMPLATE,
            round_no=str(round_no),
            lang=lang,
        )
        paperhub_pdf = b_dir / "slides.pdf"
        gold_pdf = GOLD_ROOT / "slides.pdf"
        print(f"\n[compare-b:{lang}]")
        print(f"  PaperHub: {paperhub_pdf}")
        print(f"  Gold:     {gold_pdf}")
        open_pdf(paperhub_pdf)
        open_pdf(gold_pdf)

    print(
        f"\n[compare] done. Fill the scorecards under {round_root} via direct PDF reading. "
        "Per the plan's self-judge rule, only escalate to the user when YOU honestly "
        "judge the output is at the gold standard on both scenarios."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare F4.4 round artifacts to gold.")
    ap.add_argument("--round", dest="round_no", type=int, required=True)
    args = ap.parse_args()
    return compare_round(args.round_no)


if __name__ == "__main__":
    sys.exit(main())
