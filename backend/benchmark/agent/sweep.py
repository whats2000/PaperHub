"""Run the variants × test-sets grid and report it (SRS §III-9).

Each cell (variant, bucket) is its own persisted experiment, keyed to
git_commit/stage/version/model + the bucket name. The matrix report puts variants
in columns and buckets in rows, with a Δ-vs-baseline column that flags any bucket
REGRESSION (⚠) — the "fix A breaks B/C" signal, made visible.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from benchmark.agent import corpus as corpus_mod
from benchmark.agent import store as store_mod
from benchmark.agent.eval_config import EvalConfig
from benchmark.agent.experiment import run_experiment, to_store_payload
from benchmark.agent.stages import get_stage


@dataclass
class SweepCell:
    variant: str
    testset: str
    experiment_id: int
    mean_score: float | None
    mean_tokens_in: float | None


async def run_sweep(
    cfg: EvalConfig, *, store_path: str, git_commit: str, created_at: str,
    count_tokens: Callable[[str, list[dict[str, str]]], int | None] | None = None,
) -> list[SweepCell]:
    spec = get_stage(cfg.stage)
    cells: list[SweepCell] = []
    for ts in cfg.testsets:
        cases = corpus_mod.load_corpus(ts.corpus)
        for variant in cfg.variants:
            result = await run_experiment(
                spec, variant, cases, model=cfg.model, reps=cfg.reps,
                judge_model=cfg.judge_model, prompts_dir=cfg.prompts_dir, backend=cfg.backend,
                count_tokens=count_tokens, git_commit=git_commit, created_at=created_at,
                corpus_name=ts.name, notes=f"sweep:{ts.name}")
            meta, rows = to_store_payload(result)
            exp_id = store_mod.record_experiment(store_path, meta=meta, scores=rows)
            cells.append(SweepCell(variant=result.meta.prompt_version, testset=ts.name,
                                   experiment_id=exp_id, mean_score=result.mean_score,
                                   mean_tokens_in=result.mean_tokens_in))
    return cells


def _fscore(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}"


def _ftok(x: float | None) -> str:
    return "—" if x is None else f"{x:.0f}"


def matrix_report(cfg: EvalConfig, cells: list[SweepCell]) -> str:
    spec = get_stage(cfg.stage)
    full = [f"{spec.key}/{v}" for v in cfg.variants]
    baseline = full[0]
    by = {(c.testset, c.variant): c for c in cells}
    lines = [f"# Eval sweep: {cfg.stage}", "",
             f"- model: `{cfg.model}` · reps: {cfg.reps} · backend: {cfg.backend} · variants: {', '.join(full)}",
             "- cell = mean_score / mean_tokens_in · Δ = score vs baseline (⚠ = regression)", ""]
    header = ["test set"] + [f"{v} (score/tok)" for v in full] + [f"Δ {v} vs {baseline}" for v in full[1:]]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for ts in cfg.testsets:
        base = by.get((ts.name, baseline))
        row = [ts.name]
        for v in full:
            c = by.get((ts.name, v))
            row.append(f"{_fscore(c.mean_score if c else None)} / {_ftok(c.mean_tokens_in if c else None)}")
        for v in full[1:]:
            c = by.get((ts.name, v))
            if c and base and c.mean_score is not None and base.mean_score is not None:
                d = c.mean_score - base.mean_score
                row.append(f"{d:+.2f}{' ⚠' if d < 0 else ''}")
            else:
                row.append("—")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
