"""Replay one stage with one prompt variant (SRS §III-9).

Renders messages from the eval-only variant folder + the recorded input state,
runs a single request through the executor, and maps the result to a
ReplayOutput. Used by emit_golden + ad-hoc inspection; the batched corpus path
lives in experiment.py.
"""
from __future__ import annotations

from typing import Any

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.execute import EvalRequest, ExecResult, TokenCounter, execute
from benchmark.agent.prompts import load_variant
from benchmark.agent.replay_types import ReplayOutput  # see note
from benchmark.agent.stages import StageSpec


def render_messages(system: str, user_template: str, variables: dict[str, Any],
                    history: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for h in (history or []):
        role = h.get("role")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_template.format(**variables)})
    return messages


def to_replay_output(spec: StageSpec, res: ExecResult) -> ReplayOutput:
    output = spec.output_summary(res.parsed) if (res.parsed is not None and res.error is None) else {}
    return ReplayOutput(output=output, tokens_in=res.tokens_in, error=res.error)


async def replay_stage(
    spec: StageSpec, version: str, case: CorpusCase, *,
    model: str, prompts_dir: Any, backend: str = "auto",
    count_tokens: TokenCounter | None = None,
) -> ReplayOutput:
    system, user_template = load_variant(spec.key, version, prompts_dir=prompts_dir)
    messages = render_messages(system, user_template, case.variables, case.history)
    req = EvalRequest(key=case.case_id, messages=messages, response_model=spec.response_model)
    results = await execute([req], model=model, backend=backend, count_tokens=count_tokens)
    return to_replay_output(spec, results[case.case_id])
