"""Run one experiment: a prompt variant over a corpus, N reps, batched + graded.

Builds every (case, rep) request up front and runs them as ONE batch (the
cost/throughput win), then grades each. Reps give variance so a score delta is
signal, not judge noise (SRS §III-9). Shaped by to_store_payload for the JSONL
store (Task 1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.execute import EvalRequest, TokenCounter, execute
from benchmark.agent.grade import CaseScore, JudgeFn, score_case
from benchmark.agent.prompts import load_variant
from benchmark.agent.replay import render_messages, to_replay_output
from benchmark.agent.stages import StageSpec


@dataclass
class ExperimentMeta:
    git_commit: str
    stage: str
    prompt_version: str
    model: str
    corpus: str
    reps: int
    created_at: str
    notes: str = ""


@dataclass
class ExperimentResult:
    meta: ExperimentMeta
    scores: list[CaseScore]
    mean_score: float | None
    mean_tokens_in: float | None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def run_experiment(
    spec: StageSpec, version: str, corpus: list[CorpusCase], *, model: str,
    reps: int = 1, judge_model: str | None = None, judge_fn: JudgeFn | None = None,
    prompts_dir: Any, backend: str = "auto", concurrency: int = 8,
    count_tokens: TokenCounter | None = None, git_commit: str = "unknown",
    created_at: str = "", corpus_name: str = "", notes: str = "",
) -> ExperimentResult:
    system, user_template = load_variant(spec.key, version, prompts_dir=prompts_dir)
    requests: list[EvalRequest] = []
    index: list[tuple[CorpusCase, int]] = []
    for case in corpus:
        for rep in range(reps):
            messages = render_messages(system, user_template, case.variables)
            key = f"{case.case_id}#{rep}"
            requests.append(EvalRequest(key=key, messages=messages, response_model=spec.response_model))
            index.append((case, rep))

    results = await execute(requests, model=model, backend=backend,
                            concurrency=concurrency, count_tokens=count_tokens)

    scores: list[CaseScore] = []
    for (case, rep), req in zip(index, requests, strict=True):
        replay = to_replay_output(spec, results[req.key])
        scores.append(await score_case(spec, case, replay, rep,
                                       judge_model=judge_model, judge_fn=judge_fn))

    mean_score = _mean([s.score for s in scores if s.score is not None])
    mean_tokens = _mean([float(s.tokens_in) for s in scores if s.tokens_in is not None])
    meta = ExperimentMeta(git_commit=git_commit, stage=spec.key,
                          prompt_version=f"{spec.key}/{version}", model=model,
                          corpus=corpus_name, reps=reps, created_at=created_at, notes=notes)
    return ExperimentResult(meta=meta, scores=scores, mean_score=mean_score, mean_tokens_in=mean_tokens)


def to_store_payload(result: ExperimentResult) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    n_cases = len({s.case_id for s in result.scores})
    meta = {
        "created_at": result.meta.created_at, "git_commit": result.meta.git_commit,
        "stage": result.meta.stage, "prompt_version": result.meta.prompt_version,
        "model": result.meta.model, "corpus": result.meta.corpus, "n_cases": n_cases,
        "reps": result.meta.reps, "mean_score": result.mean_score,
        "mean_tokens_in": result.mean_tokens_in, "notes": result.meta.notes,
    }
    rows = [
        {"case_id": s.case_id, "rep": s.rep, "score": s.score, "tokens_in": s.tokens_in,
         "rationale": s.rationale, "output_json": json.dumps(s.output, ensure_ascii=False),
         "error": s.error}
        for s in result.scores
    ]
    return meta, rows
