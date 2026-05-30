"""Scenario B — multi-paper conference deck. Drives /chat, dumps slides + trace.

Usage:
    uv run python -m benchmark.slide_calibration.run_multi --round 0 --lang en
    uv run python -m benchmark.slide_calibration.run_multi --round 0 --lang zh

Reads the session id from results/_sessions.json (key = "b"), which must
have been seeded via `seed.py --scenario b`.
"""
from __future__ import annotations

import argparse
import sys

from ._common import PROMPT_MULTI_PAPER_EN, PROMPT_MULTI_PAPER_ZH, load_sessions
from ._run_helpers import run_and_dump


def main() -> int:
    ap = argparse.ArgumentParser(description="Run F4.4 Scenario B — multi paper.")
    ap.add_argument("--round", dest="round_no", type=int, required=True)
    ap.add_argument("--lang", choices=["en", "zh"], required=True)
    args = ap.parse_args()

    sessions = load_sessions()
    sid = sessions.get("b")
    if sid is None:
        raise SystemExit(
            "no seeded session for scenario b. run: "
            "uv run python -m benchmark.slide_calibration.seed --scenario b"
        )

    msg = PROMPT_MULTI_PAPER_EN if args.lang == "en" else PROMPT_MULTI_PAPER_ZH
    run_and_dump(
        session_id=sid,
        user_message=msg,
        round_no=args.round_no,
        scenario="b",
        label=args.lang,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
