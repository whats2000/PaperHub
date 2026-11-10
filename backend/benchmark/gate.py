"""H4 merge gate (plan Global Constraints, SRS §III-10.4).

Computes G-a..G-d from benchmark results produced with ``--compare``: each
result dict is the CANDIDATE's (H4, ``--base-url``) and carries the BASELINE's
(v2.41, ``--compare``) run under ``compare``; ``judge`` holds the 0/1 verdict
and ``pairwise.orders`` the two pairwise verdicts ("candidate" / "baseline" /
"tie").

- G-a per scenario: each of the 8 scenarios has 3 cases; a scenario passes at
  >= 2 of 3 judge scores of 1; all 8 must pass and the total is >= 20 / 24.
- G-b no shallower: on scenarios 1, 2, 3, 6, 7, 8, the pairwise judge prefers
  the candidate or ties in >= 70 % of comparisons (each order counts once).
  Every such case contributes two comparisons: one with a ``pairwise_error``
  or no ``pairwise`` at all counts as two candidate losses, so the
  denominator is fixed by the case set.
- G-c no regression: on the ``regression`` suite (``cases.example.toml``) the
  candidate's pass count is >= the baseline's. Any regression case without a
  baseline run or with an unjudged verdict on either side fails the gate.
- G-d simple turns: the candidate's median wall time on ``chitchat`` and
  ``library_count`` cases is <= 2x the baseline's. Rows with an ``error`` on
  either side are left out of the medians; a candidate error or a case
  without a baseline run fails the gate.

A gate with no applicable cases is ``N/A``; the overall verdict is PASS only
when all four gates PASS. Merge several result files (the scenario run and the
regression run) with:

    uv run python -m benchmark.gate --results <scen>.json <regression>.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCENARIO_COUNT = 8
CASES_PER_SCENARIO = 3
SCENARIO_MIN_PASS = 2
TOTAL_MIN_PASS = 20
V241_SCENARIOS = frozenset({1, 2, 3, 6, 7, 8})
PAIRWISE_MIN_RATIO = 0.70
SIMPLE_KINDS = frozenset({"chitchat", "library_count"})
LATENCY_MAX_RATIO = 2.0
REGRESSION_SUITE = "regression"


@dataclass
class Gate:
    key: str
    status: str  # "PASS" | "FAIL" | "N/A"
    detail: str
    numbers: dict[str, Any] = field(default_factory=dict)


def _score(d: dict[str, Any] | None) -> int | None:
    j = (d or {}).get("judge") or {}
    s = j.get("score")
    return int(s) if isinstance(s, int) else None


def gate_a(results: list[dict[str, Any]]) -> Gate:
    rows = [d for d in results if d.get("scenario") is not None]
    if not rows:
        return Gate("G-a", "N/A", "no scenario cases in these results")
    per: dict[str, str] = {}
    failing: list[str] = []
    total_pass = 0
    unjudged = 0
    for s in range(1, SCENARIO_COUNT + 1):
        cases = [d for d in rows if d.get("scenario") == s]
        scores = [_score(d) for d in cases]
        unjudged += sum(1 for x in scores if x is None)
        n_pass = sum(1 for x in scores if x == 1)
        total_pass += n_pass
        per[str(s)] = f"{n_pass}/{len(cases)}"
        if len(cases) != CASES_PER_SCENARIO:
            failing.append(f"scenario {s} has {len(cases)} cases (needs {CASES_PER_SCENARIO})")
        elif n_pass < SCENARIO_MIN_PASS:
            failing.append(f"scenario {s} {n_pass}/{len(cases)}")
    total = len(rows)
    expected = SCENARIO_COUNT * CASES_PER_SCENARIO
    if total_pass < TOTAL_MIN_PASS:
        failing.append(f"total {total_pass}/{total} < {TOTAL_MIN_PASS}")
    ok = not failing
    detail = (f"{total_pass}/{total} cases pass (needs >= {TOTAL_MIN_PASS}/{expected}, "
              f"each scenario >= {SCENARIO_MIN_PASS}/{CASES_PER_SCENARIO})")
    if failing:
        detail += "; failing: " + ", ".join(failing)
    if unjudged:
        detail += f"; {unjudged} unjudged case(s) count as fails"
    return Gate("G-a", "PASS" if ok else "FAIL", detail, {
        "per_scenario": per, "total_pass": total_pass, "total": total,
        "unjudged": unjudged,
    })


def gate_b(results: list[dict[str, Any]]) -> Gate:
    rows = [d for d in results if d.get("scenario") in V241_SCENARIOS]
    if not rows:
        return Gate("G-b", "N/A", "no scenario 1, 2, 3, 6, 7, 8 cases in these results")
    orders: list[str] = []
    errors = missing = both_failed = 0
    for d in rows:
        pw = d.get("pairwise") or {}
        o = pw.get("orders")
        if isinstance(o, list) and len(o) == 2:
            orders.extend(str(x) for x in o)
            both_failed += pw.get("how") == "both-failed"
        else:
            # No verdict never shrinks the denominator: it is two H4 losses.
            orders.extend(["baseline", "baseline"])
            if d.get("pairwise_error"):
                errors += 1
            else:
                missing += 1
    favourable = sum(1 for x in orders if x in ("candidate", "tie"))
    ratio = favourable / len(orders)
    ok = ratio >= PAIRWISE_MIN_RATIO
    counts = {k: sum(1 for x in orders if x == k) for k in ("candidate", "tie", "baseline")}
    detail = (f"H4 preferred or tied in {favourable}/{len(orders)} comparisons "
              f"({ratio:.1%}, needs >= {PAIRWISE_MIN_RATIO:.0%}) over {len(rows)} case(s); "
              f"H4 {counts['candidate']} · tie {counts['tie']} · v2.41 {counts['baseline']}; "
              f"both-failed {both_failed} (ties) · pairwise errors {errors} · "
              f"missing {missing} (each = 2 H4 losses)")
    return Gate("G-b", "PASS" if ok else "FAIL", detail, {
        "favourable": favourable, "comparisons": len(orders), "ratio": ratio,
        "cases": len(rows), "both_failed": both_failed, "pairwise_errors": errors,
        "pairwise_missing": missing, **counts,
    })


def gate_c(results: list[dict[str, Any]]) -> Gate:
    rows = [d for d in results if d.get("suite") == REGRESSION_SUITE]
    if not rows:
        return Gate("G-c", "N/A", "no regression-suite cases in these results")
    not_compared = sum(1 for d in rows if not isinstance(d.get("compare"), dict))
    cand = [_score(d) for d in rows]
    base = [_score(d.get("compare")) for d in rows]
    cand_pass = sum(1 for x in cand if x == 1)
    base_pass = sum(1 for x in base if x == 1)
    # A missing verdict on either side never helps H4: the gate cannot pass.
    unjudged = sum(1 for x in cand + base if x is None)
    ok = cand_pass >= base_pass and not unjudged and not not_compared
    detail = (f"H4 {cand_pass}/{len(rows)} vs v2.41 {base_pass}/{len(rows)} "
              f"(needs H4 >= v2.41 with every verdict present); "
              f"unjudged {unjudged} · not compared {not_compared}")
    return Gate("G-c", "PASS" if ok else "FAIL", detail, {
        "candidate_pass": cand_pass, "baseline_pass": base_pass,
        "total": len(rows), "unjudged": unjudged, "not_compared": not_compared,
    })


def _elapsed(d: dict[str, Any] | None) -> float | None:
    v = (d or {}).get("elapsed_s")
    return float(v) if isinstance(v, (int, float)) else None


def gate_d(results: list[dict[str, Any]]) -> Gate:
    rows = [d for d in results if d.get("kind") in SIMPLE_KINDS]
    if not rows:
        return Gate("G-d", "N/A", "no chitchat / library_count cases in these results")
    cand: list[float] = []
    base: list[float] = []
    errored = cand_errored = not_compared = 0
    for d in rows:
        b = d.get("compare")
        a_t = _elapsed(d)
        b_t = _elapsed(b) if isinstance(b, dict) else None
        if not isinstance(b, dict) or a_t is None or b_t is None:
            not_compared += 1
            continue
        if d.get("error") or b.get("error"):
            # An errored turn's wall time says nothing about speed: left out.
            errored += 1
            cand_errored += bool(d.get("error"))
            continue
        cand.append(a_t)
        base.append(b_t)
    tail = f"; errored {errored} (H4 {cand_errored}) · not compared {not_compared}"
    if not cand:
        return Gate("G-d", "FAIL", "no usable timed case" + tail, {
            "cases": 0, "errored": errored, "candidate_errored": cand_errored,
            "not_compared": not_compared,
        })
    med_a = statistics.median(cand)
    med_b = statistics.median(base)
    if med_b > 0:
        ratio = med_a / med_b
        ok = ratio <= LATENCY_MAX_RATIO
    else:
        ratio = float("inf") if med_a > 0 else 1.0
        ok = med_a <= 0
    ok = ok and not cand_errored and not not_compared
    detail = (f"median H4 {med_a:.1f}s vs v2.41 {med_b:.1f}s = {ratio:.2f}x "
              f"(needs <= {LATENCY_MAX_RATIO:.0f}x, no H4 error) over {len(cand)} case(s)"
              + tail)
    return Gate("G-d", "PASS" if ok else "FAIL", detail, {
        "candidate_median_s": med_a, "baseline_median_s": med_b,
        "ratio": ratio, "cases": len(cand), "errored": errored,
        "candidate_errored": cand_errored, "not_compared": not_compared,
    })


def compute_gates(results: list[dict[str, Any]]) -> list[Gate]:
    return [gate_a(results), gate_b(results), gate_c(results), gate_d(results)]


def overall(gates: list[Gate]) -> str:
    return "PASS" if all(g.status == "PASS" for g in gates) else "FAIL"


def gates_markdown(gates: list[Gate]) -> str:
    lines = [
        "## H4 merge gate", "",
        f"**Overall: {overall(gates)}** (PASS needs all four gates to PASS; N/A is not a pass)",
        "", "| Gate | Status | Numbers |", "|---|---|---|",
    ]
    lines += [f"| {g.key} | **{g.status}** | {g.detail} |" for g in gates]
    a = next((g for g in gates if g.key == "G-a"), None)
    if a and a.numbers.get("per_scenario"):
        per = a.numbers["per_scenario"]
        lines += ["", "| Scenario | " + " | ".join(per) + " |",
                  "|---|" + "---|" * len(per),
                  "| Pass | " + " | ".join(per.values()) + " |"]
    return "\n".join(lines) + "\n"


def gates_json(gates: list[Gate]) -> dict[str, Any]:
    return {"overall": overall(gates), "gates": [asdict(g) for g in gates]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", nargs="+", required=True,
                    help="benchmark result .json files (run with --compare --judge)")
    ap.add_argument("--out", default="", help="optional path for the gate .json")
    args = ap.parse_args()
    results: list[dict[str, Any]] = []
    for p in args.results:
        results += json.loads(Path(p).read_text(encoding="utf-8"))
    gates = compute_gates(results)
    print(gates_markdown(gates))
    if args.out:
        Path(args.out).write_text(json.dumps(gates_json(gates), indent=2), encoding="utf-8")
        Path(args.out).with_suffix(".md").write_text(gates_markdown(gates), encoding="utf-8")
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
