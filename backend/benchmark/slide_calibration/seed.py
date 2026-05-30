"""Seed harness sessions — create + attach papers, persist session ids.

Usage:
    uv run python -m benchmark.slide_calibration.seed --scenario a --paper 2509.22093v1
    uv run python -m benchmark.slide_calibration.seed --scenario b

Scenario A creates ONE session per paper (3 total across calls).
Scenario B creates ONE session with all 3 papers attached.

Session ids are persisted to results/_sessions.json so subsequent run_*.py
calls can find them without re-seeding (re-seeding would lose the cached
session state mid-round).
"""
from __future__ import annotations

import argparse
import sys

from benchmark.driver import create_session

from ._common import (
    DEV_PAPERS,
    add_paper_by_arxiv,
    load_sessions,
    save_sessions,
)


def seed_scenario_a(arxiv_id: str) -> int:
    if arxiv_id not in DEV_PAPERS:
        raise SystemExit(f"unknown dev paper: {arxiv_id} (allowed: {DEV_PAPERS})")
    sid = create_session()
    info = add_paper_by_arxiv(sid, arxiv_id)
    print(f"[seed-a] session={sid} paper={arxiv_id} title={info.get('title')!r}")
    return sid


def seed_scenario_b() -> int:
    sid = create_session()
    for arxiv_id in DEV_PAPERS:
        info = add_paper_by_arxiv(sid, arxiv_id)
        print(f"[seed-b] session={sid} paper={arxiv_id} title={info.get('title')!r}")
    return sid


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed F4.4 calibration sessions.")
    ap.add_argument("--scenario", choices=["a", "b"], required=True)
    ap.add_argument(
        "--paper",
        help="(scenario a only) arXiv id; one of " + ", ".join(DEV_PAPERS),
    )
    args = ap.parse_args()

    sessions = load_sessions()
    if args.scenario == "a":
        if not args.paper:
            ap.error("--paper is required for scenario a")
        sid = seed_scenario_a(args.paper)
        sessions[f"a:{args.paper}"] = sid
    else:
        sid = seed_scenario_b()
        sessions["b"] = sid

    save_sessions(sessions)
    print(f"\n[seed] persisted to results/_sessions.json: {sessions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
