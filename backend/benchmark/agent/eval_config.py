"""Per-agent eval config (SRS §III-9): which prompt VARIANTS to compare over
which TEST-SET buckets. Human/Claude-editable TOML — the declarative front end
to a sweep. Matches the existing benchmark/cases.*.toml idiom.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestSet:
    name: str
    corpus: str


@dataclass
class EvalConfig:
    stage: str
    model: str
    variants: list[str]
    testsets: list[TestSet]
    reps: int = 1
    judge_model: str | None = None
    store: str = "benchmark/agent/results/experiments.jsonl"
    prompts_dir: str = "benchmark/agent/prompts"
    backend: str = "auto"


def load_eval_config(path: str | Path) -> EvalConfig:
    with Path(path).open("rb") as fh:
        raw = tomllib.load(fh)
    ev = raw.get("eval", {})
    if "stage" not in ev or "model" not in ev:
        raise ValueError("eval config: [eval] must set 'stage' and 'model'")
    variants = [str(v) for v in ev.get("variants", [])]
    if not variants:
        raise ValueError("eval config: [eval].variants must be a non-empty list")
    testsets = [TestSet(name=str(t["name"]), corpus=str(t["corpus"])) for t in raw.get("testsets", [])]
    if not testsets:
        raise ValueError("eval config: at least one [[testsets]] is required")
    return EvalConfig(stage=str(ev["stage"]), model=str(ev["model"]), variants=variants,
                      testsets=testsets, reps=int(ev.get("reps", 1)),
                      judge_model=(str(ev["judge_model"]) if ev.get("judge_model") else None),
                      store=str(ev.get("store", "benchmark/agent/results/experiments.jsonl")),
                      prompts_dir=str(ev.get("prompts_dir", "benchmark/agent/prompts")),
                      backend=str(ev.get("backend", "auto")))
