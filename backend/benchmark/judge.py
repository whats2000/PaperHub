"""LLM-as-Judge scoring for benchmark results.

Turns the human "read the answer against the cited chunks and mark 0/1" step
into an automated pass. The judge sees ONLY what a reviewer sees — the prompt,
the rubric, the answer, and the *actual cited chunk text* (the grounding
evidence the harness already collected) — and returns a structured 0/1 verdict
with a rationale. It does NOT re-call the backend; it scores an existing
results `.json`, so it's cheap to re-run and independent of the system under
test (you can judge with a stronger model than the agents used).

    uv run python -m benchmark.judge --results benchmark/results/<name>.json
    uv run python -m benchmark.judge --results <name>.json --model gemini/gemini-2.5-pro

Writes the verdicts back into the `.json` (a `judge` field per case) and
regenerates the sibling `.md` with the Score column filled + rationales.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import litellm
from pydantic import BaseModel, Field

DEFAULT_JUDGE_MODEL = "gemini/gemini-2.5-pro"
# A judge must be reproducible: pin temperature to 0 so the same answer +
# evidence yields the same verdict across runs.
JUDGE_TEMPERATURE = 0.0


class JudgeVerdict(BaseModel):
    score: int = Field(description="1 if the answer is correct AND grounded, else 0")
    confidence: float = Field(description="0.0-1.0 confidence in the verdict")
    rationale: str = Field(description="one or two sentences justifying the score")


_SYSTEM = """You are a strict evaluator for a paper-aware QA / slide-generation \
system. You score one case 0 or 1 on **correctness + grounding**.

You are given the user's request, a rubric describing what a correct answer \
must contain, the system's answer, and — for QA — the FULL TEXT of the chunks \
the answer cited (the only evidence the system retrieved).

Scoring rules:
- Score 1 ONLY IF the answer is factually correct, directly addresses the \
request, AND every substantive claim is supported by the cited chunk text \
(for QA) — no hallucinated facts, no citations that don't back the claim.
- For an honesty/negative case, the CORRECT behavior is to say the source does \
not contain the asked-for information rather than fabricating it. Score 1 for \
an honest refusal; score 0 if it invents a number/fact.
- For slides cases there are no cited chunks: score 1 if a valid, on-topic deck \
addressing the request was produced (the harness already guarantees figures are \
verify-gated). Penalize (score 0) only if the deck is off-topic, empty, or \
clearly fails an explicit instruction in the request (e.g. produces 2 slides \
when 10 were explicitly requested).
- Be strict but fair: minor stylistic issues do not lower the score; \
unsupported or wrong claims do.

Return the structured verdict."""

_USER_TEMPLATE = """## User request
{prompt}

## Rubric (what a correct answer must contain)
{rubric}

## System intent (expected / actual)
{expect_intent} / {actual_intent}

## Deck (slides cases only)
{deck}

## System answer
{answer}

## Cited chunks (grounding evidence — QA cases)
{chunks}

Score this case 0 or 1 per the rules."""


def load_env(env_path: str | Path) -> None:
    """Minimal .env loader (no python-dotenv dep). Sets keys that aren't
    already in the environment so litellm can read GEMINI_API_KEY etc."""
    p = Path(env_path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _format_chunks(cited: list[dict[str, Any]]) -> str:
    if not cited:
        return "(none — not a retrieval case, or no citations)"
    out: list[str] = []
    for c in cited:
        text = (c.get("text") or "").strip().replace("\n", " ")
        if len(text) > 1200:
            text = text[:1200] + " …"
        out.append(f"[chunk:{c['id']}] (sec={c.get('section')!r}): {text}")
    return "\n\n".join(out)


async def judge_case(d: dict[str, Any], *, model: str) -> JudgeVerdict:
    deck = d.get("deck")
    deck_str = (
        f"pages={deck.get('page_count')}, title={deck.get('title')!r}, "
        f"has_notes={deck.get('has_notes')}"
        if deck
        else "(no deck)"
    )
    user = _USER_TEMPLATE.format(
        prompt=d.get("prompt", ""),
        rubric=d.get("rubric") or "(none provided — judge on general correctness)",
        expect_intent=d.get("expect_intent"),
        actual_intent=d.get("actual_intent"),
        deck=deck_str,
        answer=d.get("final") or "(empty)",
        chunks=_format_chunks(d.get("cited_chunks") or []),
    )
    resp = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format=JudgeVerdict,
        temperature=JUDGE_TEMPERATURE,
    )
    content = resp["choices"][0]["message"]["content"]
    return JudgeVerdict.model_validate_json(content)


async def judge_results(
    results: list[dict[str, Any]], *, model: str
) -> list[dict[str, Any]]:
    """Judge each case concurrently; store the verdict under `judge`. A case
    that errored (no answer) is scored 0 without calling the model."""

    async def _one(d: dict[str, Any]) -> None:
        if d.get("error"):
            d["judge"] = {
                "score": 0,
                "confidence": 1.0,
                "rationale": f"case errored before producing an answer: {d['error'][:160]}",
                "model": "n/a",
            }
            return
        try:
            v = await judge_case(d, model=model)
            d["judge"] = {**v.model_dump(), "model": model}
        except Exception as exc:  # noqa: BLE001
            d["judge"] = {
                "score": None,
                "confidence": 0.0,
                "rationale": f"judge call failed: {exc}",
                "model": model,
            }

    await asyncio.gather(*(_one(d) for d in results))
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="path to a benchmark <name>.json")
    ap.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument(
        "--env", default=".env", help="path to .env for the LLM API key (default backend/.env)"
    )
    ap.add_argument(
        "--config",
        default="",
        help="optional benchmark config to re-render the .md with case order",
    )
    args = ap.parse_args()

    load_env(args.env)

    results_path = Path(args.results)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    print(f"Judging {len(results)} case(s) with {args.model} ...", flush=True)
    results = asyncio.run(judge_results(results, model=args.model))

    # Write verdicts back into the json.
    results_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Regenerate the .md (Score column now shows the judge verdict).
    from benchmark.config import load_config
    from benchmark.runner import _md_report

    if args.config:
        cfg = load_config(args.config)
    else:
        # Minimal cfg shim so _md_report has name/base_url.
        from benchmark.config import BenchmarkConfig

        cfg = BenchmarkConfig(name=results_path.stem, base_url="(judged)", db_path="")
    md = _md_report(cfg, results)
    md_path = results_path.with_suffix(".md")
    md_path.write_text(md, encoding="utf-8")

    scored = [d for d in results if (d.get("judge") or {}).get("score") is not None]
    total = sum((d["judge"]["score"] or 0) for d in scored)
    print(f"\nLLM-judge score: {total}/{len(scored)}")
    for d in results:
        j = d.get("judge") or {}
        print(f"  {d['id']}: {j.get('score')}  ({j.get('rationale', '')[:90]})")
    print(f"\nWrote {results_path}\nWrote {md_path}")


if __name__ == "__main__":
    main()
