"""Shared constants + helpers for the slide_calibration harness."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"

# The dev-set: same 3 papers as the D:\GitHub\Final_Report gold deck.
# Keyed by the gold deck's directory names (with v-suffix where the gold uses
# one), but mapped to the EXACT paper_content rows already in the workspace
# cache. Going via library:<pc_id> bypasses arXiv-version-suffix dispatcher
# resolution + skips re-Marker on a paper already ingested. The underlying
# paper text is the same whether the cached row is v1 vs v2 (papers don't
# meaningfully change between minor arXiv revisions for slide-generation).
DEV_PAPERS: dict[str, int] = {
    "2509.22093v1": 67,  # ADP — vision token pruning (cached id=67)
    "2512.04952v2": 62,  # FASTer — block-wise AR (cached id=62, no v-suffix)
    "2602.20200v2": 63,  # OptimusVLA — diffusion prior (cached id=63, no v-suffix)
}

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


def add_dev_paper(session_id: int, dev_key: str) -> dict:
    """Attach a dev-set paper to a session via POST /papers.

    Uses the library:<pc_id> form to attach the already-ingested cached row
    (dedup-instant; no Marker, no embeddings, no chunk recompute). Falls back
    to the dispatcher's arxiv:<id> resolution only if the dev_key isn't in the
    static DEV_PAPERS map.
    """
    pc_id = DEV_PAPERS.get(dev_key)
    paper_id_payload = f"library:{pc_id}" if pc_id else f"arxiv:{dev_key}"
    r = httpx.post(
        f"{BASE}/papers",
        json={"session_id": session_id, "paper_id": paper_id_payload},
        timeout=1800,
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
