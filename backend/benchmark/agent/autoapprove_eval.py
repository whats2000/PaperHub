"""Eval runner for the auto-approve classifier prompt (H4 Task 8).

The classifier is one structured call ``{run, reason}``, so this scores each
prompt variant (``prompts/agent_autoapprove/<ver>.yaml``) against the labelled
cases in ``corpus/agent_autoapprove.<set>.jsonl`` on the deploy small tier.
In-process, pure LLM calls (no workspace, no backend); needs a key in ``.env``.

    uv run python -m benchmark.agent.autoapprove_eval --variants v1 v4 \
        --sets dev heldout --reps 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import litellm
from pydantic import BaseModel

from benchmark.agent.prompts import DEFAULT_PROMPTS_DIR, load_variant
from paperhub.agents.core.permissions import approve_variables
from paperhub.config import small_tier_model

STAGE = "agent_autoapprove"
CORPUS_DIR = Path("benchmark/agent/corpus")


class Decision(BaseModel):
    run: bool
    reason: str


def _load_env() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def load_cases(name: str) -> list[dict[str, Any]]:
    path = CORPUS_DIR / f"{STAGE}.{name}.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


async def _one(model: str, system: str, user: str, case: dict[str, Any]) -> bool | None:
    resp = await litellm.acompletion(
        model=model, temperature=0, response_format=Decision,
        messages=[{"role": "system", "content": system},
                  # Built exactly as production builds them (escaped).
                  {"role": "user", "content": user.format(
                      **approve_variables(case["variables"]["tool"],
                                            case["variables"]["args_summary"],
                                            case["variables"]["user_message"]))}],
    )
    try:
        return Decision.model_validate_json(resp.choices[0].message.content).run
    except Exception:  # noqa: BLE001 - unparseable output counts as a miss
        return None


async def score(model: str, version: str, name: str, reps: int) -> tuple[int, int, list[str]]:
    system, user = load_variant(STAGE, version, prompts_dir=DEFAULT_PROMPTS_DIR)
    cases = load_cases(name)
    ok, misses = 0, []
    for _ in range(reps):
        got = await asyncio.gather(*[_one(model, system, user, c) for c in cases])
        for c, g in zip(cases, got, strict=True):
            if g == c["expect"]["run"]:
                ok += 1
            else:
                misses.append(f"{c['case_id']} {c['variables']['user_message']!r} -> {g}")
    return ok, len(cases) * reps, misses


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", required=True)
    ap.add_argument("--sets", nargs="+", default=["dev", "heldout"])
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    _load_env()
    model = a.model or small_tier_model()
    print("model", model)
    for v in a.variants:
        for s in a.sets:
            ok, tot, misses = await score(model, v, s, a.reps)
            print(f"{v} {s}: {ok}/{tot}")
            for m in misses:
                print("   MISS", m)


if __name__ == "__main__":
    asyncio.run(main())
