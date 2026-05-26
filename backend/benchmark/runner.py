"""Benchmark runner — drive a config of cases at the live backend, collect
grounding evidence, and emit a reviewable JSON + Markdown report.

Usage:
    uv run python -m benchmark.runner --config benchmark/cases.example.toml
    uv run python -m benchmark.runner --config <cfg> --out benchmark/results --only qa-01,rpt-02
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from benchmark import driver
from benchmark.config import BenchmarkConfig, Case, load_config
from benchmark.resolve import resolve_attach_id, title_for
from benchmark.scorer import Grounding, grounding_for


def _attach_one(cfg: BenchmarkConfig, session_id: int, key: str) -> str:
    paper_id = resolve_attach_id(cfg.db_path, key)
    if paper_id.startswith("library:"):
        info = driver.add_paper(session_id, int(paper_id.removeprefix("library:")))
    else:
        # arxiv: ingest-on-attach via the JSON paper_id path
        r = httpx.post(
            f"{cfg.base_url}/papers",
            json={"session_id": session_id, "paper_id": paper_id},
            timeout=600,
        )
        r.raise_for_status()
        info = r.json()
    return f"{key} -> {info.get('title', title_for(cfg.db_path, key) or '?')}"


@dataclasses.dataclass
class CaseResult:
    case: Case
    session_id: int
    run_id: int | None
    actual_intent: str | None
    final: str
    deck: dict[str, Any] | None
    error: str | None
    attached: list[str]
    grounding: Grounding
    elapsed_s: float


def run_case(cfg: BenchmarkConfig, case: Case, sessions: dict[str, int]) -> CaseResult:
    driver.BASE = cfg.base_url
    if case.session_group and case.session_group in sessions:
        session_id = sessions[case.session_group]
    else:
        session_id = driver.create_session()
        if case.session_group:
            sessions[case.session_group] = session_id

    attached = [_attach_one(cfg, session_id, k) for k in case.papers]

    t0 = time.monotonic()
    chat = driver.chat(session_id, case.prompt, current_view_page=case.current_view_page)
    elapsed = time.monotonic() - t0

    g = grounding_for(
        cfg.db_path,
        chat.run_id or -1,
        final_answer=chat.final,
        expect_intent=case.expect_intent,
        actual_intent=chat.intent,
        deck=chat.deck,
        search_results=chat.search_results,
    )
    return CaseResult(
        case=case,
        session_id=session_id,
        run_id=chat.run_id,
        actual_intent=chat.intent,
        final=chat.final,
        deck=chat.deck,
        error=chat.error,
        attached=attached,
        grounding=g,
        elapsed_s=elapsed,
    )


def _result_to_dict(r: CaseResult) -> dict[str, Any]:
    return {
        "id": r.case.id,
        "prompt": r.case.prompt,
        "expect_intent": r.case.expect_intent,
        "actual_intent": r.actual_intent,
        "rubric": r.case.rubric,
        "session_id": r.session_id,
        "run_id": r.run_id,
        "attached": r.attached,
        "error": r.error,
        "elapsed_s": round(r.elapsed_s, 1),
        "final": r.final,
        "deck": r.deck,
        "auto_checks": r.grounding.auto_checks,
        "notes": r.grounding.notes,
        "cited_chunk_ids": r.grounding.cited_chunk_ids,
        "cited_chunks": [dataclasses.asdict(c) for c in r.grounding.cited_chunks],
        "steps": [dataclasses.asdict(s) for s in r.grounding.steps],
    }


def _case_done(d: dict[str, Any]) -> bool:
    """A prior result counts as 'done' (skippable on resume) when it completed
    without an error and produced a run. Transient failures (connection drops →
    error set / run never created) are retried."""
    return d.get("error") in (None, "") and d.get("run_id") is not None


def _md_report(cfg: BenchmarkConfig, results: list[dict[str, Any]]) -> str:
    def _score_cell(d: dict[str, Any]) -> str:
        j = d.get("judge")
        if not j or j.get("score") is None:
            return "_TBD_"
        return f"**{j['score']}**"

    lines: list[str] = [f"# Benchmark: {cfg.name}", ""]
    lines.append(f"- Backend: `{cfg.base_url}`")
    lines.append(f"- Cases: {len(results)}")
    lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    judged = [d for d in results if (d.get("judge") or {}).get("score") is not None]
    if judged:
        total = sum((d["judge"]["score"] or 0) for d in judged)
        jmodel = judged[0]["judge"].get("model", "?")
        lines.append(f"- **LLM-judge score: {total}/{len(judged)}** (model: `{jmodel}`)")
    lines.append("")
    # summary table
    lines.append("| Case | Intent (exp/act) | Auto-checks | Run | Time | Score |")
    lines.append("|---|---|---|---|---|---|")
    for d in results:
        checks = " ".join(
            f"{'✅' if v else '❌'}{k}" for k, v in (d.get("auto_checks") or {}).items()
        )
        intent = f"{d.get('expect_intent') or '-'}/{d.get('actual_intent') or '-'}"
        lines.append(
            f"| {d['id']} | {intent} | {checks} | {d.get('run_id') or '-'} "
            f"| {d.get('elapsed_s', 0):.0f}s | {_score_cell(d)} |"
        )
    lines.append("")
    # per-case detail
    for d in results:
        lines.append(f"## {d['id']}")
        lines.append(f"**Prompt:** {d['prompt']}")
        lines.append("")
        if d.get("rubric"):
            lines.append(f"**Rubric:** {d['rubric']}")
            lines.append("")
        lines.append(f"**Attached:** {'; '.join(d.get('attached') or []) or '(none)'}")
        lines.append(
            f"**Intent:** expected `{d.get('expect_intent')}` / actual "
            f"`{d.get('actual_intent')}` · run `{d.get('run_id')}` · {d.get('elapsed_s', 0):.0f}s"
        )
        if d.get("error"):
            lines.append(f"**ERROR:** {d['error']}")
        if d.get("notes"):
            lines.append(f"**Notes:** {'; '.join(d['notes'])}")
        j = d.get("judge")
        if j and j.get("score") is not None:
            lines.append(
                f"**LLM-judge: {j['score']}** (conf {j.get('confidence')}, "
                f"`{j.get('model')}`) — {j.get('rationale', '')}"
            )
        lines.append("")
        if d.get("deck"):
            deck = d["deck"]
            lines.append(
                f"**Deck:** id={deck.get('deck_id')} pages={deck.get('page_count')} "
                f"title={deck.get('title')!r} has_notes={deck.get('has_notes')}"
            )
            lines.append("")
        lines.append("**Answer:**")
        lines.append("")
        lines.append("> " + (d.get("final") or "(empty)").replace("\n", "\n> "))
        lines.append("")
        if d.get("cited_chunks"):
            lines.append("**Cited chunks (grounding evidence):**")
            lines.append("")
            for c in d["cited_chunks"]:
                snippet = (c.get("text") or "").strip().replace("\n", " ")
                if len(snippet) > 500:
                    snippet = snippet[:500] + " …"
                lines.append(
                    f"- `chunk:{c['id']}` (sec={c.get('section')!r}, "
                    f"page={c.get('page')}): {snippet}"
                )
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default="benchmark/results")
    ap.add_argument("--only", default="", help="comma-separated case ids to run")
    ap.add_argument(
        "--resume",
        default="",
        help="path to a prior <name>.json; carries over cases that already "
        "completed (no error) and re-runs only failed/missing ones into one "
        "merged report.",
    )
    ap.add_argument(
        "--judge",
        action="store_true",
        help="after the sweep, LLM-as-Judge each case 0/1 (needs an LLM API key "
        "in backend/.env).",
    )
    ap.add_argument("--judge-model", default="", help="override the judge model")
    args = ap.parse_args()

    cfg = load_config(args.config)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    cases = [c for c in cfg.cases if not only or c.id in only]

    # Resume: load prior results; skip cases that already completed cleanly.
    prior: dict[str, dict[str, Any]] = {}
    if args.resume:
        prior_path = Path(args.resume)
        if not prior_path.exists():
            raise SystemExit(f"--resume file not found: {prior_path}")
        for d in json.loads(prior_path.read_text(encoding="utf-8")):
            prior[d["id"]] = d
        carried = [cid for cid in prior if _case_done(prior[cid])]
        print(
            f"Resume from {prior_path.name}: carrying {len(carried)} completed "
            f"case(s), retrying the rest.",
            flush=True,
        )

    # Fail fast if the backend isn't up.
    try:
        h = httpx.get(f"{cfg.base_url}/health", timeout=3)
        h.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"backend not reachable at {cfg.base_url}/health: {exc}\n"
            "Start it (scripts/start.ps1) before running the benchmark."
        ) from exc

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{cfg.name}-{ts}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    sessions: dict[str, int] = {}
    # results keeps one dict per case, in config order, mixing carried-over
    # prior dicts with freshly-run ones.
    results: list[dict[str, Any]] = []

    def _flush() -> None:
        json_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        md_path.write_text(_md_report(cfg, results), encoding="utf-8")

    for i, case in enumerate(cases, 1):
        if case.id in prior and _case_done(prior[case.id]):
            print(f"[{i}/{len(cases)}] {case.id}: carried over (resume)", flush=True)
            results.append(prior[case.id])
            _flush()
            continue
        print(f"[{i}/{len(cases)}] {case.id}: {case.prompt[:70]}...", flush=True)
        try:
            r = run_case(cfg, case, sessions)
            checks = r.grounding.auto_checks
            flag = "OK " if all(checks.values()) else "FLAG"
            print(
                f"      -> intent={r.actual_intent} run={r.run_id} {r.elapsed_s:.0f}s "
                f"[{flag}] {checks}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"      -> EXCEPTION: {exc}", flush=True)
            raise
        results.append(_result_to_dict(r))
        # Write incrementally so a long sweep (slides cases take minutes) never
        # loses completed cases if a later one hangs or the process is killed.
        _flush()

    if args.judge:
        import asyncio

        from benchmark.judge import DEFAULT_JUDGE_MODEL, judge_results, load_env

        load_env(".env")
        jmodel = args.judge_model or DEFAULT_JUDGE_MODEL
        print(f"\nLLM-judging {len(results)} case(s) with {jmodel} ...", flush=True)
        results = asyncio.run(judge_results(results, model=jmodel))
        _flush()
        judged = [d for d in results if (d.get("judge") or {}).get("score") is not None]
        total = sum((d["judge"]["score"] or 0) for d in judged)
        print(f"LLM-judge score: {total}/{len(judged)}", flush=True)

    print(f"\nWrote {json_path}\nWrote {md_path}")


if __name__ == "__main__":
    main()
