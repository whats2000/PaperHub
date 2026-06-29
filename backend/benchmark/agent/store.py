"""Append-only JSONL experiment log for per-stage prompt evaluation (§III-9).

An INTERNAL enhancement tool, not a customer surface — so it's a plain
human-readable, git-diffable JSONL file (one experiment per line, per-case
scores nested), not a binary DB. Each experiment is keyed to {git_commit, stage,
prompt_version, model} so "router/v2 raised mean 0.5 -> 1.0, tokens 120 -> 90" is
a grep/Python filter. Matches the existing benchmark/ JSON-report idiom.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_META_KEYS = (
    "id", "created_at", "git_commit", "stage", "prompt_version", "model",
    "corpus", "n_cases", "reps", "mean_score", "mean_tokens_in", "notes",
)


def _read_all(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def record_experiment(
    path: str | Path, *, meta: dict[str, Any], scores: list[dict[str, Any]],
) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_all(p)
    exp_id = max((int(e.get("id", 0)) for e in existing), default=0) + 1
    # `id` is always the assigned monotonic value, never taken from `meta`;
    # `_META_KEYS` keeps `id` only so list_experiments() summaries include it.
    record = {"id": exp_id}
    record.update({k: meta.get(k) for k in _META_KEYS if k != "id"})
    record["scores"] = scores
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return exp_id


def list_experiments(path: str | Path, stage: str | None = None) -> list[dict[str, Any]]:
    rows = [
        {k: e.get(k) for k in _META_KEYS}
        for e in _read_all(path)
        if stage is None or e.get("stage") == stage
    ]
    return list(reversed(rows))  # newest first


def get_scores(path: str | Path, experiment_id: int) -> list[dict[str, Any]]:
    for e in _read_all(path):
        if int(e.get("id", 0)) == experiment_id:
            return list(e.get("scores") or [])
    return []


def _case_means(scores: list[dict[str, Any]]) -> dict[str, float]:
    by_case: dict[str, list[float]] = {}
    for s in scores:
        if s.get("score") is not None:
            by_case.setdefault(s["case_id"], []).append(float(s["score"]))
    return {cid: sum(v) / len(v) for cid, v in by_case.items() if v}


def _flat_mean(scores: list[dict[str, Any]]) -> float | None:
    vals = [float(s["score"]) for s in scores if s.get("score") is not None]
    return sum(vals) / len(vals) if vals else None


def compare(path: str | Path, exp_a: int, exp_b: int) -> dict[str, Any]:
    a_means = _case_means(get_scores(path, exp_a))
    b_means = _case_means(get_scores(path, exp_b))
    per_case = []
    for cid in sorted(set(a_means) | set(b_means)):
        a = a_means.get(cid)
        b = b_means.get(cid)
        delta = (b - a) if (a is not None and b is not None) else None
        per_case.append({"case_id": cid, "a_score": a, "b_score": b, "delta": delta})
    a_mean = _flat_mean(get_scores(path, exp_a))
    b_mean = _flat_mean(get_scores(path, exp_b))
    mean_delta = (b_mean - a_mean) if (a_mean is not None and b_mean is not None) else None
    return {"a": exp_a, "b": exp_b, "a_mean": a_mean, "b_mean": b_mean,
            "mean_delta": mean_delta, "per_case": per_case}
