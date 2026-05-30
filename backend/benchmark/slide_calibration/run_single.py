"""Scenario A — single-paper deck. Drives /chat, dumps slides + trace.

Usage:
    uv run python -m benchmark.slide_calibration.run_single \\
        --round 0 --paper 2509.22093v1

Reads the session id from results/_sessions.json (key = "a:<arxiv_id>"),
which must have been seeded via `seed.py --scenario a --paper <id>`.
"""
from __future__ import annotations

import argparse
import sys

from ._common import DEV_PAPERS, PROMPT_SINGLE_PAPER, load_sessions
from ._run_helpers import run_and_dump


def main() -> int:
    ap = argparse.ArgumentParser(description="Run F4.4 Scenario A — single paper.")
    ap.add_argument("--round", dest="round_no", type=int, required=True)
    ap.add_argument("--paper", required=True, choices=DEV_PAPERS)
    args = ap.parse_args()

    sessions = load_sessions()
    key = f"a:{args.paper}"
    sid = sessions.get(key)
    if sid is None:
        raise SystemExit(
            f"no seeded session for {key}. run: "
            f"uv run python -m benchmark.slide_calibration.seed --scenario a --paper {args.paper}"
        )

    run_and_dump(
        session_id=sid,
        user_message=PROMPT_SINGLE_PAPER,
        round_no=args.round_no,
        scenario="a",
        label=args.paper,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
