"""Per-stage grading (SRS §III-9).

OUTPUT quality is deterministic where possible (router intent = exact match) and
an LLM judge otherwise. Judges are temp-0 for reproducibility and normalised to
0..1 so deterministic 0/1 and scalar 1-10 aggregate coherently. Calls litellm
directly (eval-local) — no deploy change.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import litellm
from pydantic import BaseModel, Field

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.replay_types import ReplayOutput
from benchmark.agent.stages import StageSpec

JUDGE_TEMPERATURE = 0.0


@dataclass
class CaseScore:
    case_id: str
    rep: int
    score: float | None
    tokens_in: int | None
    rationale: str
    output: dict[str, Any]
    error: str | None = None


class _ScalarVerdict(BaseModel):
    score: int = Field(ge=1, le=10)
    rationale: str


class _PairwiseVerdict(BaseModel):
    winner: str
    rationale: str


JudgeFn = Callable[..., Awaitable[tuple[float, str]]]


async def judge_scalar(*, request: str, rubric: str, output_text: str, model: str) -> tuple[float, str]:
    system = ("You are a strict, reproducible evaluator of one agent stage's output. "
              "Score 1 (poor) to 10 (perfect) on whether it correctly and concisely "
              "satisfies the request per the rubric. Return the structured verdict.")
    user = (f"## Request\n{request}\n\n## Rubric\n{rubric or '(general correctness)'}\n\n"
            f"## Stage output\n{output_text}\n\nScore 1-10.")
    resp = await litellm.acompletion(model=model, temperature=JUDGE_TEMPERATURE,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=_ScalarVerdict)
    v = _ScalarVerdict.model_validate_json(resp["choices"][0]["message"]["content"])
    return v.score / 10.0, v.rationale


async def judge_pairwise(*, request: str, rubric: str, output_a: str, output_b: str, model: str) -> str:
    system = ("Compare two agent-stage outputs (A and B) for the same request. Pick the "
              "better per the rubric, or 'tie'. Pairwise is more reliable than absolute "
              "scoring — be decisive.")
    user = (f"## Request\n{request}\n\n## Rubric\n{rubric or '(general correctness)'}\n\n"
            f"## Output A\n{output_a}\n\n## Output B\n{output_b}\n\nBetter: A, B, or tie?")
    resp = await litellm.acompletion(model=model, temperature=JUDGE_TEMPERATURE,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=_PairwiseVerdict)
    w = _PairwiseVerdict.model_validate_json(resp["choices"][0]["message"]["content"]).winner.strip().upper()
    return "A" if w == "A" else "B" if w == "B" else "tie"


async def score_case(
    spec: StageSpec, case: CorpusCase, replay: ReplayOutput, rep: int, *,
    judge_model: str | None = None, judge_fn: JudgeFn | None = None,
) -> CaseScore:
    if replay.error:
        return CaseScore(case.case_id, rep, 0.0, replay.tokens_in,
                         f"replay errored: {replay.error[:160]}", replay.output, replay.error)
    det = spec.deterministic_score(case.expect, replay.output)
    if det is not None:
        return CaseScore(case.case_id, rep, det, replay.tokens_in, "deterministic check", replay.output)
    if judge_model is None:
        return CaseScore(case.case_id, rep, None, replay.tokens_in,
                         "no deterministic check and no judge configured", replay.output)
    fn = judge_fn or judge_scalar
    score, rationale = await fn(request=str(case.variables.get("user_message", "")),
                                rubric=case.rubric, output_text=str(replay.output), model=judge_model)
    return CaseScore(case.case_id, rep, score, replay.tokens_in, rationale, replay.output)
