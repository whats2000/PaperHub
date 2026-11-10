"""LLM-as-Judge scoring for benchmark results.

Turns the human "read the answer against the cited chunks and mark 0/1" step
into an automated pass. The judge sees ONLY what a reviewer sees — the seeded
earlier turns, the prompt, the rubric, the answer, and the *actual cited chunk
text* (the grounding evidence the harness already collected) — and returns three
0/1 criteria (correct, grounded, on_thread); a case scores 1 only when all three
hold. It does NOT re-call the backend; it scores an existing
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

from paperhub.agents.core.figures import encode_image
from paperhub.agents.core.page_tools import render_region

DEFAULT_JUDGE_MODEL = "gemini/gemini-2.5-pro"
# A judge must be reproducible: pin temperature to 0 so the same answer +
# evidence yields the same verdict across runs.
JUDGE_TEMPERATURE = 0.0
# Cited chunks reach the judge whole (stored chunks are <= 5000 chars); this is
# only a guard. A 1200-char cut hid the supporting sentence of long chunks and
# flipped grounding verdicts.
CHUNK_CHARS = 12000


class JudgeVerdict(BaseModel):
    correct: int = Field(description="1 if the answer is right and does what the request asks, else 0")
    grounded: int = Field(description="1 if every substantive claim is supported by the cited chunks, else 0")
    on_thread: int = Field(description="1 if the answer continues the conversation's subject, else 0")
    confidence: float = Field(description="0.0-1.0 confidence in the verdict")
    rationale: str = Field(description="one or two sentences; name the criterion that failed")

    @property
    def score(self) -> int:
        """A case scores 1 only when it is correct, grounded AND on the thread."""
        return int(self.correct == 1 and self.grounded == 1 and self.on_thread == 1)


# Prompt chosen by the judge_thread comparison
# (benchmark/agent/prompts/judge_thread/README.md): v2 over the v1 minimal edit
# — same accuracy, per-criterion verdicts say WHICH criterion failed.
_SYSTEM = """You grade one answer from a paper-reading research assistant on three \
criteria, each 1 or 0.

1. correct — the answer is factually right and does what the request asks. \
A negative case is one where the rubric says the sources lack the asked-for \
fact: there, saying so honestly is correct and inventing a value is not. When the \
rubric gives the answer, an answer that says it could not find it, or that the paper \
does not contain it, is wrong (correct = 0) however honestly it is phrased; so is an \
answer that gives only part of what the rubric requires. For slides, correct = a \
valid, on-topic deck \
that follows explicit instructions (e.g. a requested slide count).
2. grounded — every substantive claim is supported by the text of the chunks \
the answer cites, or, for a claim cited as [fig:P/ID] or [view:P/N/box], by that \
figure's or page region's image in <cited_figures> (values read off a plot count as supported when the image \
shows them within reading accuracy). A true claim its citations do not \
support fails. Slides \
cases have no chunks: grounded = 1 when a valid on-topic deck was produced.
3. on_thread — the answer continues the conversation in <prior_turns>. A \
follow-up ("more detail on that", "what about the second paper?") must be \
answered about the subject the conversation was on, not generically or about \
another paper. A request that names a new subject may switch topic. With no \
prior turns, on_thread = 1.

Use the rubric for what a correct answer must contain. Ignore style; penalise \
wrong, unsupported or off-thread content. In the rationale, name the criterion \
that failed.

Examples (on_thread):
- Prior turns discuss GISC's sparsity loss. Request: "more detail on the \
experiment setup". Answer describes GISC's datasets and training schedule → 1. \
Answer lists generic steps for designing experiments → 0.
- Prior turns compare paper A and paper B. Request: "what about the second \
one's limitations?" Answer covers paper B's limitations → 1; paper A's → 0.
- Prior turns discuss paper A. Request: "Separately, how does ReMoE route \
tokens?" Answer covers ReMoE → 1 (the user switched topic)."""

_USER_TEMPLATE = """<prior_turns>
{history}
</prior_turns>

<request>
{prompt}
</request>

<rubric>
{rubric}
</rubric>

<deck>{deck}</deck>

<answer>
{answer}
</answer>

<cited_chunks>
{chunks}
</cited_chunks>

<cited_figures>
{figures}
</cited_figures>"""


def format_history(turns: list[dict[str, Any]] | None) -> str:
    """Seeded turns (``[{user, assistant}]``) as a readable transcript."""
    if not turns:
        return "(none — first turn)"
    return "\n\n".join(
        f"User: {t.get('user', '')}\nAssistant: {t.get('assistant', '')}" for t in turns
    )


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
        return "(none)"
    out: list[str] = []
    for c in cited:
        text = (c.get("text") or "").strip().replace("\n", " ")
        if len(text) > CHUNK_CHARS:
            text = text[:CHUNK_CHARS] + " …"
        out.append(f"[chunk:{c['id']}] (sec={c.get('section')!r}): {text}")
    return "\n\n".join(out)


def _view_parts(cited: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """``[view:]`` regions for the judge: a line each, plus the rendered region."""
    lines: list[str] = []
    parts: list[dict[str, Any]] = []
    for v in cited:
        token = f"[view:{v['key']}]"
        if not v.get("pdf_path"):
            lines.append(f"{token} (page region; image missing)")
            continue
        try:
            img = render_region(Path(v["pdf_path"]), int(v["page"]),
                                tuple(v["box"]), paper=int(v["paper_content_id"]))
        except (IndexError, ValueError, RuntimeError):   # page beyond the PDF, bad box
            lines.append(f"{token} (page region; image missing)")
            continue
        lines.append(f"{token} (region of page {v['page']}; image attached below)")
        parts.append({"type": "text", "text": f"Image of {token}:"})
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:{img.mime};base64,{img.data_b64}"}})
    return "\n".join(lines), parts


def _figure_parts(cited: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """The ``<cited_figures>`` text and one image part per figure that still
    has its image (attached after the text, each headed by its key)."""
    if not cited:
        return "(none)", []
    lines: list[str] = []
    parts: list[dict[str, Any]] = []
    for f in cited:
        cap = " ".join(str(f.get("caption") or "").split())
        shown = "image attached below" if f.get("image_path") else "image missing"
        lines.append(f"[fig:{f['key']}] ({shown}): {cap or '(no caption)'}")
        if f.get("image_path"):
            mime, data, _size, _sha = encode_image(Path(f["image_path"]))
            parts.append({"type": "text", "text": f"Image of [fig:{f['key']}]:"})
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{mime};base64,{data}"}})
    return "\n".join(lines), parts


async def judge_case(d: dict[str, Any], *, model: str) -> JudgeVerdict:
    deck = d.get("deck")
    deck_str = (
        f"pages={deck.get('page_count')}, title={deck.get('title')!r}, "
        f"has_notes={deck.get('has_notes')}"
        if deck
        else "(no deck)"
    )
    figures_text, image_parts = _figure_parts(d.get("cited_figures") or [])
    views_text, view_parts = _view_parts(d.get("cited_views") or [])
    if views_text:
        figures_text = views_text if figures_text == "(none)" else f"{figures_text}\n{views_text}"
        image_parts += view_parts
    user = _USER_TEMPLATE.format(
        history=format_history(d.get("turns")),
        prompt=d.get("prompt", ""),
        rubric=d.get("rubric") or "(none provided — judge on general correctness)",
        deck=deck_str,
        answer=d.get("final") or "(empty)",
        chunks=_format_chunks(d.get("cited_chunks") or []),
        figures=figures_text,
    )
    content: str | list[dict[str, Any]] = (
        [{"type": "text", "text": user}, *image_parts] if image_parts else user)
    resp = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": content},
        ],
        response_format=JudgeVerdict,
        temperature=JUDGE_TEMPERATURE,
    )
    content = resp["choices"][0]["message"]["content"]
    return JudgeVerdict.model_validate_json(content)


def all_answers(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every answer to judge: each case plus its ``--compare`` baseline run."""
    out: list[dict[str, Any]] = []
    for d in results:
        out.append(d)
        if isinstance(d.get("compare"), dict):
            out.append(d["compare"])
    return out


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
            d["judge"] = {"score": v.score, **v.model_dump(), "model": model}
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
    asyncio.run(judge_results(all_answers(results), model=args.model))
    compared = any(isinstance(d.get("compare"), dict) for d in results)
    if compared:
        # --compare results: re-run the pairwise verdicts too, then the gate.
        from benchmark.runner import pairwise_results, write_gate

        print("Pairwise-judging candidate vs baseline ...", flush=True)
        asyncio.run(pairwise_results(results, model=args.model))

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
    if compared:
        from benchmark.gate import gates_markdown

        print("\n" + gates_markdown(write_gate(results, results_path)), flush=True)

    scored = [d for d in results if (d.get("judge") or {}).get("score") is not None]
    total = sum((d["judge"]["score"] or 0) for d in scored)
    print(f"\nLLM-judge score: {total}/{len(scored)}")
    for d in results:
        j = d.get("judge") or {}
        print(f"  {d['id']}: {j.get('score')}  ({j.get('rationale', '')[:90]})")
    print(f"\nWrote {results_path}\nWrote {md_path}")


if __name__ == "__main__":
    main()
