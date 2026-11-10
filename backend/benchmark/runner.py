"""Benchmark runner — drive a config of cases at the live backend, collect
grounding evidence, and emit a reviewable JSON + Markdown report.

Usage:
    uv run python -m benchmark.runner --config benchmark/cases.example.toml
    uv run python -m benchmark.runner --config <cfg> --out benchmark/results --only qa-01,rpt-02

Version comparison (H4 merge gate): ``--base-url`` is the CANDIDATE, ``--compare``
the BASELINE instance; every case runs on both, the baseline's result is stored
under the case's ``compare`` key, ``--judge`` also writes pairwise verdicts (both
orders) and ``<stem>.gate.json`` holds G-a..G-d (see ``benchmark/gate.py``):

    uv run python -m benchmark.runner --config benchmark/cases.h4-scenarios.toml
        --base-url http://127.0.0.1:8100 --db-path D:/paperhub-h4-workspace/paperhub.db
        --compare http://127.0.0.1:8200
        --compare-db-path D:/paperhub-v241-eval-workspace/paperhub.db --judge
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

from benchmark import driver, gate
from benchmark.agent.grade import judge_pairwise
from benchmark.config import BenchmarkConfig, Case, history_for, load_config
from benchmark.judge import CHUNK_CHARS, format_history
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


def run_case(
    cfg: BenchmarkConfig, case: Case, sessions: dict[str, int],
    created: list[int] | None = None,
) -> CaseResult:
    """Run one case. Every session this call creates is appended to ``created``
    so the caller can delete it afterwards."""
    driver.BASE = cfg.base_url
    if case.session_id is not None:
        # Slide-aware QA: reuse an existing session that already has a built
        # deck + enabled refs. Skip session creation + paper attach entirely.
        session_id = case.session_id
        attached: list[str] = []
    else:
        if case.session_group and case.session_group in sessions:
            session_id = sessions[case.session_group]
        else:
            session_id = driver.create_session()
            if created is not None:
                created.append(session_id)
            if case.session_group:
                sessions[case.session_group] = session_id
        attached = [_attach_one(cfg, session_id, k) for k in case.papers]

    t0 = time.monotonic()
    chat = driver.chat(
        session_id, case.prompt,
        current_view_page=case.current_view_page,
        slide_attached=case.slide_attached,
        history=history_for(case),
    )
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


def _result_to_dict(r: CaseResult, cfg: BenchmarkConfig) -> dict[str, Any]:
    return {
        "id": r.case.id,
        "base_url": cfg.base_url,
        "suite": cfg.suite,
        "scenario": r.case.scenario,
        "kind": r.case.kind,
        "turns": [dataclasses.asdict(t) for t in r.case.turns],
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
        "cited_figures": [dataclasses.asdict(f) for f in r.grounding.cited_figures],
        "cited_views": [dataclasses.asdict(v) for v in r.grounding.cited_views],
        "steps": [dataclasses.asdict(s) for s in r.grounding.steps],
    }


def _delete_sessions(session_ids: list[int], base_url: str | None = None) -> None:
    """Delete every session the run created; one failure never skips the rest."""
    for sid in session_ids:
        try:
            driver.delete_session(sid, base=base_url)
        except Exception as exc:  # noqa: BLE001
            print(f"warning: could not delete session {sid}: {exc}", flush=True)
    if session_ids:
        print(f"Deleted {len(session_ids)} benchmark session(s).", flush=True)


def _case_done(d: dict[str, Any], *, compare: bool = False) -> bool:
    """A prior result counts as 'done' (skippable on resume) when it completed
    without an error and produced a run — on both instances when comparing.
    Transient failures (connection drops → error set / run never created) are
    retried."""
    ok = d.get("error") in (None, "") and d.get("run_id") is not None
    if compare:
        b = d.get("compare")
        ok = ok and isinstance(b, dict) and _case_done(b)
    return ok


def _pairwise_output(d: dict[str, Any]) -> str:
    """One side's answer + the text of the chunks it cited, for the pairwise judge."""
    chunks = "\n".join(
        f"[chunk:{c['id']}] {(c.get('text') or '').strip()[:CHUNK_CHARS]}"
        for c in d.get("cited_chunks") or []
    ) or "(none)"
    deck = d.get("deck")
    deck_line = (f"\n<deck>pages={deck.get('page_count')}, title={deck.get('title')!r}</deck>"
                 if deck else "")
    return (f"<answer>\n{d.get('final') or '(empty)'}\n</answer>{deck_line}\n"
            f"<cited_chunks>\n{chunks}\n</cited_chunks>")


def _unusable(d: dict[str, Any]) -> bool:
    return bool(d.get("error")) or not (d.get("final") or d.get("deck"))


async def pairwise_case(d: dict[str, Any], *, model: str) -> dict[str, Any]:
    """Pairwise-judge the candidate (``d``) against its baseline
    (``d['compare']``) in both orders. Each order's verdict is mapped to
    "candidate" / "baseline" / "tie"; a side that errored or answered nothing
    loses both orders without a judge call (both unusable → two ties)."""
    b = d["compare"]
    bad_a, bad_b = _unusable(d), _unusable(b)
    if bad_a or bad_b:
        w = "tie" if bad_a and bad_b else ("baseline" if bad_a else "candidate")
        orders = [w, w]
        how = "both-failed" if bad_a and bad_b else "deterministic"
    else:
        request = (f"<conversation>\n{format_history(d.get('turns'))}\n</conversation>\n"
                   f"<request>\n{d.get('prompt', '')}\n</request>")
        rubric = d.get("rubric") or ""
        out_a, out_b = _pairwise_output(d), _pairwise_output(b)
        first = await judge_pairwise(request=request, rubric=rubric,
                                     output_a=out_a, output_b=out_b, model=model)
        second = await judge_pairwise(request=request, rubric=rubric,
                                      output_a=out_b, output_b=out_a, model=model)
        orders = [
            {"A": "candidate", "B": "baseline"}.get(first, "tie"),
            {"A": "baseline", "B": "candidate"}.get(second, "tie"),
        ]
        how = "judge"
    verdict = orders[0] if orders[0] == orders[1] else "tie"
    return {"orders": orders, "verdict": verdict, "how": how, "model": model}


async def pairwise_results(results: list[dict[str, Any]], *, model: str) -> None:
    """Write ``pairwise`` on every compared case of a scenario v2.41 can attempt
    (the G-b set). A judge failure leaves no verdict and is recorded under
    ``pairwise_error``; the gate counts it as two H4 losses (ruling R23)."""
    import asyncio

    async def _one(d: dict[str, Any]) -> None:
        try:
            d["pairwise"] = await pairwise_case(d, model=model)
            d.pop("pairwise_error", None)
        except Exception as exc:  # noqa: BLE001
            d.pop("pairwise", None)
            d["pairwise_error"] = f"pairwise judge failed: {exc}"[:300]

    await asyncio.gather(*(
        _one(d) for d in results
        if isinstance(d.get("compare"), dict) and d.get("scenario") in gate.V241_SCENARIOS
    ))


def write_gate(results: list[dict[str, Any]], json_path: Path) -> list[gate.Gate]:
    """Write ``<stem>.gate.json`` next to the results and return the gates."""
    gates = gate.compute_gates(results)
    json_path.with_suffix(".gate.json").write_text(
        json.dumps(gate.gates_json(gates), indent=2), encoding="utf-8"
    )
    return gates


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
    compared = any(isinstance(d.get("compare"), dict) for d in results)
    if compared:
        bj = [d["compare"] for d in results
              if isinstance(d.get("compare"), dict)
              and (d["compare"].get("judge") or {}).get("score") is not None]
        if bj:
            btotal = sum((b["judge"]["score"] or 0) for b in bj)
            lines.append(f"- **Baseline (compare) LLM-judge score: {btotal}/{len(bj)}**")
    lines.append("")
    if compared:
        lines.append(gate.gates_markdown(gate.compute_gates(results)))
        lines.append("| Case | Scen. | Kind | Run (H4/base) | Time (H4/base) "
                     "| Score (H4/base) | Pairwise |")
        lines.append("|---|---|---|---|---|---|---|")
        for d in results:
            b = d.get("compare") or {}
            pw = (d.get("pairwise") or {}).get("orders")
            lines.append(
                f"| {d['id']} | {d.get('scenario') or '-'} | {d.get('kind') or '-'} "
                f"| {d.get('run_id') or '-'}/{b.get('run_id') or '-'} "
                f"| {d.get('elapsed_s', 0):.0f}s/{b.get('elapsed_s', 0):.0f}s "
                f"| {_score_cell(d)}/{_score_cell(b)} | {', '.join(pw) if pw else '-'} |"
            )
    else:
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
        if d.get("turns"):
            lines.append("**Seeded turns:**")
            lines.append("")
            for t in d["turns"]:
                said = (t.get("assistant") or "").replace("\n", " ")
                lines.append(f"- User: {t.get('user', '')}")
                lines.append(f"  - Assistant: {said}")
            lines.append("")
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
                f"**LLM-judge: {j['score']}** {_criteria(j)}(conf {j.get('confidence')}, "
                f"`{j.get('model')}`) — {j.get('rationale', '')}"
            )
        pw = d.get("pairwise")
        if pw:
            lines.append(f"**Pairwise (H4 vs baseline):** {pw.get('orders')} → "
                         f"{pw.get('verdict')} ({pw.get('how')})")
        elif d.get("pairwise_error"):
            lines.append(f"**Pairwise:** {d['pairwise_error']}")
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
        b = d.get("compare")
        if isinstance(b, dict):
            lines.append(
                f"**Baseline (`{b.get('base_url')}`):** run `{b.get('run_id')}` · "
                f"{b.get('elapsed_s', 0):.0f}s · intent `{b.get('actual_intent')}`"
            )
            if b.get("error"):
                lines.append(f"**Baseline ERROR:** {b['error']}")
            bjudge = b.get("judge")
            if bjudge and bjudge.get("score") is not None:
                lines.append(f"**Baseline LLM-judge: {bjudge['score']}** {_criteria(bjudge)}— "
                             f"{bjudge.get('rationale', '')}")
            lines.append("")
            lines.append("> " + (b.get("final") or "(empty)").replace("\n", "\n> "))
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _criteria(j: dict[str, Any]) -> str:
    keys = ("correct", "grounded", "on_thread")
    if not all(k in j for k in keys):
        return ""
    return "(" + " ".join(f"{k}={j[k]}" for k in keys) + ") "


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
    ap.add_argument(
        "--keep-sessions",
        action="store_true",
        help="keep the chat sessions the run created (e.g. to review a deck in "
        "the frontend); by default they are deleted at the end.",
    )
    ap.add_argument("--base-url", default="", help="override the config's backend URL")
    ap.add_argument("--db-path", default="", help="override the config's workspace DB path")
    ap.add_argument(
        "--compare",
        default="",
        help="BASELINE backend URL (e.g. v2.41 on :8200): every case also runs "
        "there; --base-url is the candidate. With --judge, pairwise verdicts "
        "(both orders) and the G-a..G-d gate are written.",
    )
    ap.add_argument(
        "--compare-db-path",
        default="",
        help="the baseline instance's workspace DB (required with --compare)",
    )
    args = ap.parse_args()
    if args.compare and not args.compare_db_path:
        ap.error("--compare needs --compare-db-path (the baseline's own workspace DB)")

    cfg = load_config(args.config)
    if args.base_url or args.db_path:
        cfg = dataclasses.replace(
            cfg,
            base_url=args.base_url or cfg.base_url,
            db_path=args.db_path or cfg.db_path,
        )
    cfg_b: BenchmarkConfig | None = None
    if args.compare:
        cfg_b = dataclasses.replace(
            cfg, base_url=args.compare.rstrip("/"), db_path=args.compare_db_path,
        )
    comparing = cfg_b is not None
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    cases = [c for c in cfg.cases if not only or c.id in only]
    if comparing:
        reused = [c.id for c in cases if c.session_id is not None]
        if reused:
            # A session id names a row in ONE workspace; the two instances'
            # workspace copies are not guaranteed to share ids.
            ap.error(f"--compare cannot run cases that reuse a session_id: {reused}")

    # Resume: load prior results; skip cases that already completed cleanly.
    prior: dict[str, dict[str, Any]] = {}
    if args.resume:
        prior_path = Path(args.resume)
        if not prior_path.exists():
            raise SystemExit(f"--resume file not found: {prior_path}")
        for d in json.loads(prior_path.read_text(encoding="utf-8")):
            prior[d["id"]] = d
        carried = [cid for cid in prior if _case_done(prior[cid], compare=comparing)]
        print(
            f"Resume from {prior_path.name}: carrying {len(carried)} completed "
            f"case(s), retrying the rest.",
            flush=True,
        )

    # Fail fast if a backend isn't up.
    for url in [cfg.base_url] + ([cfg_b.base_url] if cfg_b else []):
        try:
            h = httpx.get(f"{url}/health", timeout=3)
            h.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"backend not reachable at {url}/health: {exc}\n"
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
        if comparing:
            write_gate(results, json_path)

    # Sessions this run created, per instance; deleted at the end, also on a
    # failure or an interrupt (sessions reused through `session_id` are not
    # ours to delete).
    created: list[int] = []
    created_b: list[int] = []
    sessions_b: dict[str, int] = {}
    driver.BASE = cfg.base_url
    try:
        for i, case in enumerate(cases, 1):
            if case.id in prior and _case_done(prior[case.id], compare=comparing):
                print(f"[{i}/{len(cases)}] {case.id}: carried over (resume)", flush=True)
                results.append(prior[case.id])
                _flush()
                continue
            print(f"[{i}/{len(cases)}] {case.id}: {case.prompt[:70]}...", flush=True)
            try:
                r = run_case(cfg, case, sessions, created)
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
            row = _result_to_dict(r, cfg)
            if cfg_b is not None:
                try:
                    rb = run_case(cfg_b, case, sessions_b, created_b)
                except Exception as exc:  # noqa: BLE001
                    print(f"      -> baseline EXCEPTION: {exc}", flush=True)
                    raise
                print(
                    f"      -> baseline run={rb.run_id} {rb.elapsed_s:.0f}s "
                    f"(candidate {r.elapsed_s:.0f}s)",
                    flush=True,
                )
                row["compare"] = _result_to_dict(rb, cfg_b)
            results.append(row)
            # Write incrementally so a long sweep (slides cases take minutes) never
            # loses completed cases if a later one hangs or the process is killed.
            _flush()

        if args.judge:
            import asyncio

            from benchmark.judge import DEFAULT_JUDGE_MODEL, all_answers, judge_results, load_env

            load_env(".env")
            jmodel = args.judge_model or DEFAULT_JUDGE_MODEL
            answers = all_answers(results)
            print(f"\nLLM-judging {len(answers)} answer(s) with {jmodel} ...", flush=True)
            asyncio.run(judge_results(answers, model=jmodel))
            _flush()
            judged = [d for d in results if (d.get("judge") or {}).get("score") is not None]
            total = sum((d["judge"]["score"] or 0) for d in judged)
            print(f"LLM-judge score: {total}/{len(judged)}", flush=True)
            if comparing:
                print(f"Pairwise-judging H4 vs baseline with {jmodel} ...", flush=True)
                asyncio.run(pairwise_results(results, model=jmodel))
                _flush()
        if comparing:
            gates = write_gate(results, json_path)
            print("\n" + gate.gates_markdown(gates), flush=True)
    finally:
        if args.keep_sessions:
            if created or created_b:
                print(f"Kept {len(created) + len(created_b)} benchmark session(s): "
                      f"{created} {created_b}", flush=True)
        else:
            _delete_sessions(created, cfg.base_url)
            if cfg_b is not None:
                _delete_sessions(created_b, cfg_b.base_url)

    print(f"\nWrote {json_path}\nWrote {md_path}")


if __name__ == "__main__":
    main()
