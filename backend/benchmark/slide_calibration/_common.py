"""Shared constants + helpers for the slide_calibration harness."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"

# The dev-set: same 3 arXiv papers as the D:\GitHub\Final_Report gold deck.
DEV_PAPERS: list[str] = [
    "2509.22093v1",  # ADP — vision token pruning
    "2512.04952v2",  # FASTer — block-wise AR
    "2602.20200v2",  # OptimusVLA — diffusion prior
]

# Prompts the harness sends. Deterministic so cross-round diffs are meaningful.
PROMPT_SINGLE_PAPER = (
    "Please prepare ~15 Beamer slides covering this paper for an ML research audience."
)
PROMPT_MULTI_PAPER_EN = (
    "Please prepare a 12-minute conference talk covering these three papers, "
    "focused on the links between them and a proposed direction."
)
PROMPT_MULTI_PAPER_ZH = (
    "請為這三篇論文做一份 12 分鐘的會議簡報，"
    "重點放在它們之間的連結與你提出的方向。"
)

# Where artifacts land.
RESULTS_DIR = Path(__file__).parent / "results"
SESSIONS_FILE = RESULTS_DIR / "_sessions.json"


def add_paper_by_arxiv(session_id: int, arxiv_id: str) -> dict:
    """Attach an arXiv paper to a session via POST /papers.

    Goes through the same dispatch as the frontend's "add a paper" path —
    ingests via the standard pipeline if not cached, dedup-hits otherwise.
    Long timeout because cold ingestion of a long paper takes minutes.
    """
    r = httpx.post(
        f"{BASE}/papers",
        json={"session_id": session_id, "paper_id": f"arxiv:{arxiv_id}"},
        timeout=600,
    )
    r.raise_for_status()
    return r.json()


def load_sessions() -> dict[str, int]:
    """Load the seeded session-id map (key = scenario label, value = session id)."""
    if not SESSIONS_FILE.exists():
        return {}
    return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))


def save_sessions(sessions: dict[str, int]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(
        json.dumps(sessions, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def round_dir(round_no: int, scenario: str, label: str) -> Path:
    """results/round_N/<scenario>/<label>/ — created on first call."""
    p = RESULTS_DIR / f"round_{round_no:02d}" / scenario / label
    p.mkdir(parents=True, exist_ok=True)
    return p
