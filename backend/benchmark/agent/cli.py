"""CLI for per-stage prompt evaluation (SRS §III-9). In-process — no live backend
needed (calls the LLM via litellm directly); needs an LLM API key in backend/.env.

    uv run python -m benchmark.agent.cli harvest --db workspace/paperhub.db --stage router --out benchmark/agent/corpus/router.harvest.jsonl
    uv run python -m benchmark.agent.cli run --stage router --version v1 --corpus benchmark/agent/corpus/router.core.jsonl --model gemini/gemini-2.5-flash
    uv run python -m benchmark.agent.cli compare --a 1 --b 2
    uv run python -m benchmark.agent.cli list --stage router
    uv run python -m benchmark.agent.cli golden --stage router --version v2 --corpus <c> --model <m> --out <g.jsonl>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from benchmark.agent import corpus as corpus_mod
from benchmark.agent import store
from benchmark.agent.eval_config import load_eval_config
from benchmark.agent.experiment import run_experiment as _run_experiment
from benchmark.agent.experiment import to_store_payload
from benchmark.agent.stages import STAGE_REGISTRY, get_stage
from benchmark.agent.sweep import matrix_report, run_sweep

_emit_golden = corpus_mod.emit_golden

DEFAULT_STORE = "benchmark/agent/results/experiments.jsonl"
DEFAULT_PROMPTS = "benchmark/agent/prompts"


def _token_counter(model: str, messages: list[dict[str, str]]) -> int | None:
    from benchmark.agent.execute import _default_count_tokens
    return _default_count_tokens(model, messages)


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_env(env_path: str) -> None:
    from benchmark.judge import load_env
    load_env(env_path)


def _cmd_harvest(args: argparse.Namespace) -> int:
    run_ids = [int(x) for x in args.run_ids.split(",")] if args.run_ids else None
    cases = corpus_mod.harvest(args.db, args.stage, run_ids=run_ids, limit=args.limit)
    corpus_mod.save_corpus(args.out, cases)
    print(f"Harvested {len(cases)} {args.stage} case(s) -> {args.out}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    _load_env(args.env)
    spec = get_stage(args.stage)
    cases = corpus_mod.load_corpus(args.corpus)
    result = asyncio.run(_run_experiment(
        spec, args.version, cases, model=args.model, reps=args.reps,
        judge_model=(args.judge_model or None), prompts_dir=args.prompts_dir,
        backend=args.backend, count_tokens=_token_counter, git_commit=_git_commit(),
        created_at=datetime.now().isoformat(timespec="seconds"),
        corpus_name=Path(args.corpus).stem, notes=args.notes))
    meta, rows = to_store_payload(result)
    exp_id = store.record_experiment(args.store, meta=meta, scores=rows)
    print(f"experiment {exp_id}: {result.meta.prompt_version} model={args.model} "
          f"mean_score={result.mean_score} mean_tokens_in={result.mean_tokens_in} "
          f"(n={meta['n_cases']}, reps={args.reps})")
    return 0


def _cmd_golden(args: argparse.Namespace) -> int:
    _load_env(args.env)
    spec = get_stage(args.stage)
    cases = corpus_mod.load_corpus(args.corpus)
    golden = asyncio.run(_emit_golden(spec, args.version, cases, model=args.model,
                                      prompts_dir=args.prompts_dir, backend=args.backend,
                                      count_tokens=_token_counter))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("w", encoding="utf-8") as fh:
        for g in golden:
            fh.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"Wrote {len(golden)} golden output(s) -> {args.out}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    cmp = store.compare(args.store, args.a, args.b)
    print(f"compare exp {args.a} -> {args.b}: mean {cmp['a_mean']} -> {cmp['b_mean']} (delta {cmp['mean_delta']})")
    for p in cmp["per_case"]:
        print(f"  {p['case_id']}: {p['a_score']} -> {p['b_score']} (delta {p['delta']})")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    for e in store.list_experiments(args.store, stage=(args.stage or None)):
        print(f"  [{e['id']}] {e['created_at']} {e['prompt_version']} model={e['model']} "
              f"commit={e['git_commit']} corpus={e['corpus']} mean_score={e['mean_score']} "
              f"mean_tokens_in={e['mean_tokens_in']} (n={e['n_cases']}, reps={e['reps']})")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    _load_env(args.env)
    cfg = load_eval_config(args.config)
    now = datetime.now()
    cells = asyncio.run(run_sweep(cfg, store_path=cfg.store, git_commit=_git_commit(),
                                  created_at=now.isoformat(timespec="seconds"),
                                  count_tokens=_token_counter))
    report = matrix_report(cfg, cells)
    out = args.out or f"benchmark/agent/results/{cfg.stage}-sweep-{now.strftime('%Y%m%d-%H%M%S')}.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy codepage (e.g. cp950) that cannot
    # encode the matrix report's ⚠ marker; force UTF-8 so printing never crashes
    # the CLI on its own success output. Guarded: a captured/!reconfigurable
    # stream (e.g. under pytest capsys) simply skips this.
    for _stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(prog="benchmark.agent.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    stages = sorted(STAGE_REGISTRY)
    backends = ["auto", "concurrent", "batch_api"]

    h = sub.add_parser("harvest", help="build a per-stage corpus from the trace DB")
    h.add_argument("--db", required=True)
    h.add_argument("--stage", required=True, choices=stages)
    h.add_argument("--out", required=True)
    h.add_argument("--run-ids", default="")
    h.add_argument("--limit", type=int, default=200)
    h.set_defaults(fn=_cmd_harvest)

    r = sub.add_parser("run", help="run a variant over a corpus + persist an experiment")
    r.add_argument("--stage", required=True, choices=stages)
    r.add_argument("--version", required=True)
    r.add_argument("--corpus", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--judge-model", default="")
    r.add_argument("--store", default=DEFAULT_STORE)
    r.add_argument("--prompts-dir", default=DEFAULT_PROMPTS)
    r.add_argument("--backend", default="auto", choices=backends)
    r.add_argument("--env", default=".env")
    r.add_argument("--notes", default="")
    r.set_defaults(fn=_cmd_run)

    g = sub.add_parser("golden", help="emit a frozen variant's golden outputs")
    g.add_argument("--stage", required=True, choices=stages)
    g.add_argument("--version", required=True)
    g.add_argument("--corpus", required=True)
    g.add_argument("--model", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--prompts-dir", default=DEFAULT_PROMPTS)
    g.add_argument("--backend", default="auto", choices=backends)
    g.add_argument("--env", default=".env")
    g.set_defaults(fn=_cmd_golden)

    c = sub.add_parser("compare", help="diff two experiments")
    c.add_argument("--store", default=DEFAULT_STORE)
    c.add_argument("--a", type=int, required=True)
    c.add_argument("--b", type=int, required=True)
    c.set_defaults(fn=_cmd_compare)

    li = sub.add_parser("list", help="list experiments")
    li.add_argument("--store", default=DEFAULT_STORE)
    li.add_argument("--stage", default="")
    li.set_defaults(fn=_cmd_list)

    sw = sub.add_parser("sweep", help="run a variants x test-sets grid from a TOML config")
    sw.add_argument("--config", required=True)
    sw.add_argument("--out", default="")
    sw.add_argument("--env", default=".env")
    sw.set_defaults(fn=_cmd_sweep)

    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
