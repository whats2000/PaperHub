# Plan G1 — Agent Chain Eval (cascade per-stage prompt evaluation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, **fully-isolated** engine that evaluates one agent prompt at a time, precisely, on real recorded inputs — replay a stage with a chosen prompt *variant* (a YAML file in an eval-only folder), score its output (quality + token count), persist each run as a comparable JSONL experiment, and sweep variants × test-set buckets in one cost-aware batch — and prove the loop on the **Router** stage.

**Architecture:** A new `backend/benchmark/agent/` package that touches **no deploy code**. Prompt variants live in an eval-only folder (`benchmark/agent/prompts/<stage>/<version>.yaml`); the executor renders from that folder and calls `litellm` directly (auto-using the provider Batch API where available, degrading to concurrent requests otherwise). Experiments persist to an append-only `results/experiments.jsonl`. This is Phase-1 of SRS §III-9 (per-stage engine + router pilot). Adopting a winning prompt into the production registry is an explicit, separate, post-sweep human decision — **not** automated here.

**Tech Stack:** Python 3.11, `uv`, `litellm` (already a dep — `acompletion` + Batch API `acreate_file`/`acreate_batch`/`aretrieve_batch`/`afile_content`), stdlib `json` (JSONL store), `pydantic`, `pytest` + `pytest-asyncio`. No new third-party dependency.

## Global Constraints

- **ISOLATION — ZERO deploy-code impact (load-bearing, the user's hard rule).** Every file this plan creates lives under `backend/benchmark/agent/`, `backend/tests/benchmark_agent/`, or eval data files. The eval system **MUST NOT edit any `backend/src/paperhub/**` file or any production prompt YAML.** It MAY *read-only* `import` production response models (e.g. `from paperhub.models.domain import RoutingDecision`) to parse output, and *read* the workspace DB (`tool_calls`). It calls `litellm` directly — it does **not** modify or route through the production `LiteLlmAdapter`. Promoting a winning variant into `src/.../llm/prompts/` is out of scope: a separate, deliberate `writing-agent-prompts` step after a sweep. **A diff that changes any `src/` file fails this plan's review.**
- **Python tooling:** `uv` only — never `pip`/system python. From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`. (`mypy --strict` runs on `src`; this plan touches no `src`, so that gate is unaffected. `ruff check … tests` covers the new `tests/benchmark_agent/` — keep it clean. The `benchmark/agent/` package is outside both gates, like the rest of `benchmark/`; write it typed + clean anyway.)
- **Test discipline (TDD):** failing-test-first → minimal impl → green → commit, every task. **No unit test calls a real LLM/network** — monkeypatch `litellm` (`acompletion` and the batch fns). The real-API run happens once, at the end (Task 12).
- **Per-task test scope:** run only the new test file(s) + targeted `ruff`, not the full suite (full suite only at plan-phase completion — expensive).
- **Commits:** Conventional Commits — `feat(eval):`, `test(eval):`, `docs(eval):`. Body wraps at 72 cols. Trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Shell:** PowerShell (`;` to chain). Bash tool available for POSIX scripts.
- **Never persist the rendered prompt** — store the `{placeholder}` input state (`variables`); replay re-renders from state + the variant YAML.
- **Restricted ops:** local commits/branches proceed freely; `git push`/PR/merge need explicit per-instance approval.

---

## File Structure

**New package — `backend/benchmark/agent/`** (one responsibility per file):

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker (empty). |
| `store.py` | Append-only **JSONL** experiment log (`results/experiments.jsonl`): `record_experiment`, `list_experiments`, `get_scores`, `compare`. No stages/LLM knowledge. |
| `stages.py` | `StageSpec` (key, trace ids, response model, 3 per-stage callables) + `STAGE_REGISTRY` with the **router** entry. Read-only imports `RoutingDecision`. |
| `corpus.py` | `CorpusCase` + `harvest()` (read `tool_calls` → cases) + `load_corpus`/`save_corpus` (JSONL) + `emit_golden()` (freeze + propagate). |
| `prompts.py` | `load_variant(stage, version, prompts_dir)` — read an eval-only `prompts/<stage>/<version>.yaml` (`system`/`user`). The browsable variant store. |
| `execute.py` | Swappable executor: `execute(requests, …, backend="auto")` — auto-uses the provider **Batch API** where the provider supports it, **degrades to concurrent** `litellm.acompletion` otherwise (or on any batch failure). Calls litellm directly; no production adapter. |
| `replay.py` | `render_messages()` + `ReplayOutput` + `replay_stage()` (single-call via the executor; used by `emit_golden` + inspection). |
| `grade.py` | `CaseScore` + `score_case()` (deterministic first, scalar-judge fallback) + `judge_scalar`/`judge_pairwise` (temp-0, normalized 0..1). |
| `experiment.py` | `run_experiment()` — build all requests → `execute` (batched) → grade → aggregate; `to_store_payload()`. |
| `eval_config.py` | TOML loader: `[eval]` (stage, model, reps, judge_model, store, prompts_dir, backend, variants) + `[[testsets]]`. The human/Claude-editable "which prompts × which test sets." |
| `sweep.py` | `run_sweep()` — build the **whole** variants × buckets grid as one batch, persist each cell, render the matrix report (Δ vs baseline + ⚠ regression). |
| `cli.py` | `argparse` `harvest | run | sweep | compare | list | golden` with `--backend`, `--prompts-dir`, JSONL `--store`. |
| `prompts/router/v1.yaml` | Committed baseline variant — a **copy** of the shipped `router_v1.yaml` (so v1 = production baseline). Experimental `v2.yaml`… are dropped in beside it. |
| `corpus/router.{core,regression,edge}.jsonl` | Committed hand-labeled router corpora by bucket. Harvested failures → gitignored `router.harvest.jsonl`. |
| `router.eval.toml` | Committed sweep config: variants × the three buckets. |
| `results/` | Gitignored (existing `results/` rule) — `experiments.jsonl` + sweep `.md` reports. |
| `README.md` | The loop, CLI verbs, the isolation rule, pointer to SRS §III-9. |

**No `src/` changes. No `.gitignore` change** (`results/` and the harvest pattern are covered — Task 1 verifies; `agent/corpus/*.harvest.jsonl` is added only if not already matched).

**New tests — `backend/tests/benchmark_agent/`** (`__init__.py` + one `test_*.py` per package file).

### Shared types (defined once, referenced by later tasks)

```python
# stages.py
@dataclass(frozen=True)
class StageSpec:
    key: str                                   # 'router' — also the prompts/<key>/ folder
    trace_agent: str                           # tool_calls.agent, e.g. 'router'
    trace_tool: str                            # tool_calls.tool,  e.g. 'classify'
    response_model: type[BaseModel] | None     # RoutingDecision for structured stages
    variables_from_args: Callable[[dict[str, Any]], dict[str, Any]]
    output_summary: Callable[[Any], dict[str, Any]]
    deterministic_score: Callable[[dict[str, Any], dict[str, Any]], float | None]
    # prompt_version label is f"{key}/{version}"

# corpus.py
@dataclass
class CorpusCase:
    case_id: str
    stage: str
    variables: dict[str, Any]
    expect: dict[str, Any]
    rubric: str = ""
    source_run_id: int | None = None
    observed: dict[str, Any] | None = None

# execute.py
@dataclass
class EvalRequest:
    key: str
    messages: list[dict[str, str]]
    response_model: type[BaseModel] | None

@dataclass
class ExecResult:
    key: str
    parsed: Any | None          # response_model instance, or raw str, or None on error
    tokens_in: int | None
    error: str | None = None
    backend: str = "concurrent"

# replay.py
@dataclass
class ReplayOutput:
    output: dict[str, Any]
    tokens_in: int | None
    error: str | None = None

# grade.py
@dataclass
class CaseScore:
    case_id: str
    rep: int
    score: float | None         # 0..1 (deterministic 0/1, or judge 1-10 ÷ 10)
    tokens_in: int | None
    rationale: str
    output: dict[str, Any]
    error: str | None = None

# experiment.py
@dataclass
class ExperimentMeta:
    git_commit: str
    stage: str
    prompt_version: str         # 'router/v1'
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

# eval_config.py
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

# sweep.py
@dataclass
class SweepCell:
    variant: str                # full label 'router/v1'
    testset: str
    experiment_id: int
    mean_score: float | None
    mean_tokens_in: float | None
```

---

## Task 1: JSONL experiment store

**Files:**
- Create: `backend/benchmark/agent/__init__.py` (empty)
- Create: `backend/benchmark/agent/store.py`
- Create: `backend/tests/benchmark_agent/__init__.py` (empty)
- Test: `backend/tests/benchmark_agent/test_store.py`
- Modify (only if needed): `backend/benchmark/.gitignore` — ensure `results/` ignores `agent/results/` (it does — pattern is recursive); append `agent/corpus/*.harvest.jsonl`.

**Interfaces:**
- Produces: `record_experiment(path: str | Path, *, meta: dict[str, Any], scores: list[dict[str, Any]]) -> int`; `list_experiments(path, stage: str | None = None) -> list[dict[str, Any]]`; `get_scores(path, experiment_id: int) -> list[dict[str, Any]]`; `compare(path, exp_a: int, exp_b: int) -> dict[str, Any]`.
- One JSONL line per experiment: `{id, created_at, git_commit, stage, prompt_version, model, corpus, n_cases, reps, mean_score, mean_tokens_in, notes, scores:[{case_id, rep, score, tokens_in, rationale, output_json, error}, …]}`. `id` = 1-based monotonic in the file.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_store.py
from benchmark.agent import store


def _meta(**over):
    base = dict(
        created_at="2026-06-29T10:00:00", git_commit="abc123", stage="router",
        prompt_version="router/v1", model="gemini/gemini-2.5-flash",
        corpus="core", n_cases=2, reps=1, mean_score=0.5, mean_tokens_in=120.0, notes="",
    )
    base.update(over)
    return base


def _scores():
    return [
        {"case_id": "r1", "rep": 0, "score": 1.0, "tokens_in": 100, "rationale": "hit",
         "output_json": '{"intent":"paper_qa"}', "error": None},
        {"case_id": "r2", "rep": 0, "score": 0.0, "tokens_in": 140, "rationale": "miss",
         "output_json": '{"intent":"slides"}', "error": None},
    ]


def test_record_then_list_and_get(tmp_path):
    p = tmp_path / "experiments.jsonl"
    exp_id = store.record_experiment(p, meta=_meta(), scores=_scores())
    assert exp_id == 1
    exp2 = store.record_experiment(p, meta=_meta(prompt_version="router/v2", mean_score=1.0), scores=_scores())
    assert exp2 == 2  # monotonic id
    rows = store.list_experiments(p, stage="router")
    assert len(rows) == 2 and rows[0]["id"] == 2  # newest first
    assert {r["prompt_version"] for r in rows} == {"router/v1", "router/v2"}
    assert "scores" not in rows[0]  # summaries omit nested scores
    scores = store.get_scores(p, 1)
    assert {s["case_id"] for s in scores} == {"r1", "r2"}


def test_compare(tmp_path):
    p = tmp_path / "experiments.jsonl"
    a = store.record_experiment(p, meta=_meta(prompt_version="router/v1"), scores=[
        {"case_id": "r1", "rep": 0, "score": 0.0, "tokens_in": 100, "rationale": "", "output_json": "{}", "error": None}])
    b = store.record_experiment(p, meta=_meta(prompt_version="router/v2"), scores=[
        {"case_id": "r1", "rep": 0, "score": 1.0, "tokens_in": 80, "rationale": "", "output_json": "{}", "error": None}])
    cmp = store.compare(p, a, b)
    assert cmp["mean_delta"] == 1.0
    per = {x["case_id"]: x for x in cmp["per_case"]}
    assert per["r1"]["a_score"] == 0.0 and per["r1"]["b_score"] == 1.0 and per["r1"]["delta"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/store.py
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


def compare(path: str | Path, exp_a: int, exp_b: int) -> dict[str, Any]:
    a_means = _case_means(get_scores(path, exp_a))
    b_means = _case_means(get_scores(path, exp_b))
    per_case = []
    for cid in sorted(set(a_means) | set(b_means)):
        a = a_means.get(cid)
        b = b_means.get(cid)
        delta = (b - a) if (a is not None and b is not None) else None
        per_case.append({"case_id": cid, "a_score": a, "b_score": b, "delta": delta})
    a_mean = sum(a_means.values()) / len(a_means) if a_means else None
    b_mean = sum(b_means.values()) / len(b_means) if b_means else None
    mean_delta = (b_mean - a_mean) if (a_mean is not None and b_mean is not None) else None
    return {"a": exp_a, "b": exp_b, "a_mean": a_mean, "b_mean": b_mean,
            "mean_delta": mean_delta, "per_case": per_case}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_store.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Confirm gitignore covers results + harvest**

Read `backend/benchmark/.gitignore`. The existing `results/` line already ignores `benchmark/agent/results/` (recursive match). Append one line for harvested corpora:

```
agent/corpus/*.harvest.jsonl
```

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_store.py
git add benchmark/agent/__init__.py benchmark/agent/store.py \
        tests/benchmark_agent/__init__.py tests/benchmark_agent/test_store.py \
        benchmark/.gitignore
git commit -m "feat(eval): add JSONL experiment store for per-stage prompt eval"
```

---

## Task 2: StageSpec registry (router stage spec)

**Files:**
- Create: `backend/benchmark/agent/stages.py`
- Test: `backend/tests/benchmark_agent/test_stages.py`

**Interfaces:**
- Consumes: read-only `from paperhub.models.domain import RoutingDecision`.
- Produces: `StageSpec` (per Shared types); `STAGE_REGISTRY: dict[str, StageSpec]`; `get_stage(key) -> StageSpec`. Router: `key="router"`, `trace_agent="router"`, `trace_tool="classify"`, `response_model=RoutingDecision`; `variables_from_args` → `{user_message, enabled_refs_count, slide_attached}`; `output_summary(RoutingDecision)` → `{intent, resolved_query, response_language, confidence}`; `deterministic_score(expect, output)` → `1.0`/`0.0` on `intent` match, `None` if `expect` lacks `intent`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_stages.py
from paperhub.models.domain import RoutingDecision

from benchmark.agent.stages import STAGE_REGISTRY, get_stage


def test_router_spec_registered():
    spec = get_stage("router")
    assert spec.key == "router"
    assert spec.trace_agent == "router" and spec.trace_tool == "classify"
    assert spec.response_model is RoutingDecision
    assert "router" in STAGE_REGISTRY


def test_router_variables_from_args():
    spec = get_stage("router")
    args = {"user_message": "compare these", "enabled_refs_count": 2, "slide_attached": False, "$x": "ignored"}
    assert spec.variables_from_args(args) == {
        "user_message": "compare these", "enabled_refs_count": 2, "slide_attached": False}


def test_router_output_and_score():
    spec = get_stage("router")
    d = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                        reasoning="x", resolved_query="q", response_language="English")
    out = spec.output_summary(d)
    assert out["intent"] == "paper_qa" and out["resolved_query"] == "q"
    assert spec.deterministic_score({"intent": "paper_qa"}, out) == 1.0
    assert spec.deterministic_score({"intent": "slides"}, out) == 0.0
    assert spec.deterministic_score({}, out) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_stages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.stages'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/stages.py
"""Per-stage evaluation specs (SRS §III-9).

What a stage needs to be replayed + scored in isolation: how it appears in the
tool_calls trace, its structured output model, and three small callables. Plan G1
ships the router; downstream stages are added in G2. Read-only imports of the
production response model — NO deploy-code change.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from paperhub.models.domain import RoutingDecision


@dataclass(frozen=True)
class StageSpec:
    key: str
    trace_agent: str
    trace_tool: str
    response_model: type[BaseModel] | None
    variables_from_args: Callable[[dict[str, Any]], dict[str, Any]]
    output_summary: Callable[[Any], dict[str, Any]]
    deterministic_score: Callable[[dict[str, Any], dict[str, Any]], float | None]


def _router_variables(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_message": args["user_message"],
        "enabled_refs_count": args.get("enabled_refs_count", 0),
        "slide_attached": args.get("slide_attached", False),
    }


def _router_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, RoutingDecision) else RoutingDecision.model_validate(obj)
    return {"intent": d.intent, "resolved_query": d.resolved_query,
            "response_language": d.response_language, "confidence": d.confidence}


def _router_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    want = expect.get("intent")
    if want is None:
        return None
    return 1.0 if output.get("intent") == want else 0.0


ROUTER = StageSpec(
    key="router", trace_agent="router", trace_tool="classify",
    response_model=RoutingDecision, variables_from_args=_router_variables,
    output_summary=_router_output, deterministic_score=_router_score,
)

STAGE_REGISTRY: dict[str, StageSpec] = {ROUTER.key: ROUTER}


def get_stage(key: str) -> StageSpec:
    if key not in STAGE_REGISTRY:
        raise KeyError(f"unknown stage {key!r}; known: {sorted(STAGE_REGISTRY)}")
    return STAGE_REGISTRY[key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_stages.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_stages.py
git add benchmark/agent/stages.py tests/benchmark_agent/test_stages.py
git commit -m "feat(eval): add StageSpec registry with the router stage"
```

---

## Task 3: Corpus — harvest from trace + JSONL IO

**Files:**
- Create: `backend/benchmark/agent/corpus.py`
- Test: `backend/tests/benchmark_agent/test_corpus.py`

**Interfaces:**
- Consumes: `get_stage` (Task 2); `tool_calls` columns.
- Produces: `CorpusCase` (Shared types); `harvest(db_path, stage, *, run_ids=None, limit=200) -> list[CorpusCase]`; `save_corpus(path, cases)`; `load_corpus(path) -> list[CorpusCase]`. `emit_golden` is added in Task 9. `harvest` reads `agent=spec.trace_agent AND tool=spec.trace_tool`, maps args via `spec.variables_from_args`, seeds `observed` from the recorded result and `expect={"intent": observed["intent"]}` for the router. `case_id = f"run{run_id}-s{step_index}"`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_corpus.py
import json
import sqlite3

from benchmark.agent import corpus
from benchmark.agent.corpus import CorpusCase

_DDL = """
CREATE TABLE runs (id INTEGER PRIMARY KEY);
CREATE TABLE tool_calls (
    run_id INTEGER, step_index INTEGER, agent TEXT, tool TEXT, model TEXT,
    args_redacted_json TEXT, result_summary_json TEXT, status TEXT
);
"""


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    conn.execute("INSERT INTO runs (id) VALUES (7)")
    conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, model, "
        "args_redacted_json, result_summary_json, status) VALUES (?,?,?,?,?,?,?,?)",
        (7, 0, "router", "classify", "gemini/gemini-2.5-flash",
         json.dumps({"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}),
         json.dumps({"intent": "paper_qa", "resolved_query": "what is MHA?", "response_language": "English", "confidence": 0.9}),
         "ok"))
    conn.execute("INSERT INTO tool_calls (run_id, step_index, agent, tool, status) "
                 "VALUES (7,1,'research','paper_qa:synthesize','ok')")
    conn.commit()
    conn.close()


def test_harvest_router(tmp_path):
    db = tmp_path / "paperhub.db"
    _seed(db)
    cases = corpus.harvest(db, "router")
    assert len(cases) == 1
    c = cases[0]
    assert c.variables == {"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}
    assert c.expect == {"intent": "paper_qa"}
    assert c.observed and c.observed["intent"] == "paper_qa"
    assert c.source_run_id == 7 and c.case_id == "run7-s0"


def test_save_load_roundtrip(tmp_path):
    cases = [CorpusCase(case_id="x1", stage="router",
                        variables={"user_message": "hi", "enabled_refs_count": 0, "slide_attached": False},
                        expect={"intent": "chitchat"}, rubric="greeting")]
    p = tmp_path / "router.core.jsonl"
    corpus.save_corpus(p, cases)
    assert corpus.load_corpus(p) == cases
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.corpus'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/corpus.py
"""Per-stage eval corpus — real inputs harvested from the trace, JSONL on disk.

Inputs come from ACTUAL runs (tool_calls) — never synthesized — so a stage is
measured on what it really sees, and a failed run can be promoted verbatim
(SRS §III-9). ``expect`` is seeded from the recorded output as a starting label;
for a promoted *failure* a human corrects it. Read-only DB access — no deploy
change.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from benchmark.agent.stages import get_stage


@dataclass
class CorpusCase:
    case_id: str
    stage: str
    variables: dict[str, Any]
    expect: dict[str, Any]
    rubric: str = ""
    source_run_id: int | None = None
    observed: dict[str, Any] | None = field(default=None)


def harvest(
    db_path: str | Path, stage: str, *,
    run_ids: list[int] | None = None, limit: int = 200,
) -> list[CorpusCase]:
    spec = get_stage(stage)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT run_id, step_index, args_redacted_json, result_summary_json "
            "FROM tool_calls WHERE agent = ? AND tool = ? AND args_redacted_json IS NOT NULL"
        )
        params: list[Any] = [spec.trace_agent, spec.trace_tool]
        if run_ids:
            sql += f" AND run_id IN ({','.join('?' * len(run_ids))})"
            params += list(run_ids)
        sql += " ORDER BY run_id DESC, step_index ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    cases: list[CorpusCase] = []
    for r in rows:
        try:
            variables = spec.variables_from_args(json.loads(r["args_redacted_json"]))
        except (json.JSONDecodeError, KeyError):
            continue
        observed: dict[str, Any] | None = None
        expect: dict[str, Any] = {}
        if r["result_summary_json"]:
            try:
                observed = json.loads(r["result_summary_json"])
                if stage == "router" and isinstance(observed, dict) and "intent" in observed:
                    expect = {"intent": observed["intent"]}
            except json.JSONDecodeError:
                observed = None
        cases.append(CorpusCase(
            case_id=f"run{r['run_id']}-s{r['step_index']}", stage=stage,
            variables=variables, expect=expect, source_run_id=int(r["run_id"]), observed=observed))
    return cases


def save_corpus(path: str | Path, cases: list[CorpusCase]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")


def load_corpus(path: str | Path) -> list[CorpusCase]:
    out: list[CorpusCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(CorpusCase(**json.loads(line)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_corpus.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_corpus.py
git add benchmark/agent/corpus.py tests/benchmark_agent/test_corpus.py
git commit -m "feat(eval): harvest per-stage eval corpus from the trace (JSONL IO)"
```

---

## Task 4: Prompt-variant loader (the browsable YAML folder)

**Files:**
- Create: `backend/benchmark/agent/prompts.py`
- Test: `backend/tests/benchmark_agent/test_prompts.py`

**Interfaces:**
- Produces: `load_variant(stage: str, version: str, *, prompts_dir: str | Path) -> tuple[str, str]` — read `<prompts_dir>/<stage>/<version>.yaml` (keys `system`, `user`), return `(system, user_template)`. `list_variants(stage, prompts_dir) -> list[str]` — sorted variant names present. Raises `FileNotFoundError` with a clear message if the file is missing.
- `DEFAULT_PROMPTS_DIR = "benchmark/agent/prompts"`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_prompts.py
import pytest

from benchmark.agent import prompts


def _write(d, stage, version, system, user):
    p = d / stage
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{version}.yaml").write_text(f"system: |\n  {system}\nuser: |\n  {user}\n", encoding="utf-8")


def test_load_variant(tmp_path):
    _write(tmp_path, "router", "v1", "You classify intent.", "MSG: {user_message}")
    system, user = prompts.load_variant("router", "v1", prompts_dir=tmp_path)
    assert "classify intent" in system
    assert user.strip() == "MSG: {user_message}"


def test_list_variants(tmp_path):
    _write(tmp_path, "router", "v1", "a", "b")
    _write(tmp_path, "router", "v2", "a", "b")
    assert prompts.list_variants("router", prompts_dir=tmp_path) == ["v1", "v2"]


def test_missing_variant_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        prompts.load_variant("router", "v9", prompts_dir=tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.prompts'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/prompts.py
"""Eval-only prompt-variant store: a browsable folder of YAML files (§III-9).

Experimental prompt variants live in ``<prompts_dir>/<stage>/<version>.yaml``
(``system:`` + ``user:`` blocks) so a human or Claude can open, read, and edit a
variant to "experience the performance". This is SEPARATE from the production
registry under ``src/.../llm/prompts/`` — the eval never touches deploy code.
The baseline ``router/v1.yaml`` is seeded as a copy of the shipped prompt;
adopting a winner = copy its YAML back into the registry (a separate step).
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_PROMPTS_DIR = "benchmark/agent/prompts"


def load_variant(stage: str, version: str, *, prompts_dir: str | Path) -> tuple[str, str]:
    path = Path(prompts_dir) / stage / f"{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"prompt variant not found: {path} — create it (system:/user: blocks) "
            f"or seed it from the registry's {stage}_{version}.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return str(data["system"]), str(data["user"])


def list_variants(stage: str, *, prompts_dir: str | Path) -> list[str]:
    d = Path(prompts_dir) / stage
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_prompts.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_prompts.py
git add benchmark/agent/prompts.py tests/benchmark_agent/test_prompts.py
git commit -m "feat(eval): browsable YAML-folder prompt-variant loader"
```

---

## Task 5: Swappable executor (auto Batch API → degrade to concurrent)

**Files:**
- Create: `backend/benchmark/agent/execute.py`
- Test: `backend/tests/benchmark_agent/test_execute.py`

**Interfaces:**
- Produces: `EvalRequest`, `ExecResult` (Shared types); `BATCH_CAPABLE_PROVIDERS: frozenset[str]`; `async execute(requests: list[EvalRequest], *, model: str, backend: str = "auto", concurrency: int = 8, count_tokens=None, poll_interval: float = 15.0, timeout_s: float = 86400.0) -> dict[str, ExecResult]`.
- Backend selection: `auto` → use the provider Batch API iff `litellm.get_llm_provider(model)[1] ∈ BATCH_CAPABLE_PROVIDERS`, else concurrent; any batch failure **degrades to concurrent**. `concurrent` forces concurrent. `batch_api` forces batch (still degrades on failure). Calls `litellm` **directly** — no production adapter (isolation rule).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_execute.py
import pytest
from pydantic import BaseModel

from benchmark.agent import execute
from benchmark.agent.execute import EvalRequest


class _Out(BaseModel):
    intent: str


def _reqs():
    return [EvalRequest(key="c1", messages=[{"role": "user", "content": "q"}], response_model=_Out)]


@pytest.mark.asyncio
async def test_concurrent_parses_structured(monkeypatch):
    async def _fake_acompletion(**kw):
        return {"choices": [{"message": {"content": '{"intent":"paper_qa"}'}}]}
    monkeypatch.setattr(execute.litellm, "acompletion", _fake_acompletion)
    res = await execute.execute(_reqs(), model="ollama/llama3", backend="concurrent",
                                count_tokens=lambda m, msgs: 42)
    assert res["c1"].parsed.intent == "paper_qa"
    assert res["c1"].tokens_in == 42 and res["c1"].error is None and res["c1"].backend == "concurrent"


@pytest.mark.asyncio
async def test_concurrent_records_error(monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("provider 500")
    monkeypatch.setattr(execute.litellm, "acompletion", _boom)
    res = await execute.execute(_reqs(), model="ollama/llama3", backend="concurrent",
                                count_tokens=lambda m, msgs: 1)
    assert res["c1"].parsed is None and "provider 500" in res["c1"].error


@pytest.mark.asyncio
async def test_auto_uses_batch_for_capable_provider(monkeypatch):
    # Provider 'openai' is batch-capable -> the batch path runs.
    monkeypatch.setattr(execute.litellm, "get_llm_provider", lambda model: (model, "openai", None, None))
    calls = {}

    class _Obj:
        def __init__(self, **kw): self.__dict__.update(kw)

    async def _acreate_file(**kw):
        calls["file"] = True
        return _Obj(id="file-1")

    async def _acreate_batch(**kw):
        calls["batch"] = kw
        return _Obj(id="batch-1", status="validating", output_file_id=None)

    async def _aretrieve_batch(**kw):
        return _Obj(id="batch-1", status="completed", output_file_id="out-1")

    async def _afile_content(**kw):
        line = '{"custom_id":"c1","response":{"status_code":200,"body":{"choices":[{"message":{"content":"{\\"intent\\":\\"slides\\"}"}}]}}}'
        return _Obj(content=line.encode("utf-8"))

    monkeypatch.setattr(execute.litellm, "acreate_file", _acreate_file)
    monkeypatch.setattr(execute.litellm, "acreate_batch", _acreate_batch)
    monkeypatch.setattr(execute.litellm, "aretrieve_batch", _aretrieve_batch)
    monkeypatch.setattr(execute.litellm, "afile_content", _afile_content)
    res = await execute.execute(_reqs(), model="gpt-4o", backend="auto",
                                count_tokens=lambda m, msgs: 7, poll_interval=0)
    assert calls.get("file") and "batch" in calls
    assert res["c1"].parsed.intent == "slides" and res["c1"].backend == "batch_api"


@pytest.mark.asyncio
async def test_batch_failure_degrades_to_concurrent(monkeypatch):
    monkeypatch.setattr(execute.litellm, "get_llm_provider", lambda model: (model, "openai", None, None))

    async def _acreate_file(**kw):
        raise RuntimeError("batch upload unsupported")

    async def _fake_acompletion(**kw):
        return {"choices": [{"message": {"content": '{"intent":"paper_qa"}'}}]}
    monkeypatch.setattr(execute.litellm, "acreate_file", _acreate_file)
    monkeypatch.setattr(execute.litellm, "acompletion", _fake_acompletion)
    res = await execute.execute(_reqs(), model="gpt-4o", backend="auto",
                                count_tokens=lambda m, msgs: 1, poll_interval=0)
    assert res["c1"].parsed.intent == "paper_qa" and res["c1"].backend == "concurrent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_execute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.execute'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/execute.py
"""Swappable eval executor (SRS §III-9).

Runs a list of EvalRequests against a model and returns one ExecResult per key.
Backend selection is AUTOMATIC: use the provider Batch API where the provider
supports it (~50% cheaper, async), and DEGRADE to concurrent normal requests
otherwise — or whenever a batch step fails. Calls ``litellm`` DIRECTLY (it does
NOT route through the production LiteLlmAdapter) so the eval has zero deploy
footprint. Structured output is parsed eval-side (native response_format, with a
JSON-mode fallback).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import litellm

logger = logging.getLogger(__name__)

# Providers whose litellm batch path we trust; everything else degrades to
# concurrent. Extend as litellm's batch coverage grows.
BATCH_CAPABLE_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "azure", "vertex_ai", "bedrock", "anthropic"})


@dataclass
class EvalRequest:
    key: str
    messages: list[dict[str, str]]
    response_model: type | None


@dataclass
class ExecResult:
    key: str
    parsed: Any | None
    tokens_in: int | None
    error: str | None = None
    backend: str = "concurrent"


TokenCounter = Callable[[str, list[dict[str, str]]], int | None]


def _default_count_tokens(model: str, messages: list[dict[str, str]]) -> int | None:
    try:
        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:  # noqa: BLE001 — best-effort
        return None


def _provider_of(model: str) -> str:
    try:
        return str(litellm.get_llm_provider(model)[1])
    except Exception:  # noqa: BLE001
        return ""


_FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$")


def _extract_json(text: str) -> str:
    s = text.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    a, b = s.find("{"), s.rfind("}")
    return s[a:b + 1] if (a != -1 and b > a) else s


async def _structured_call(model: str, messages: list[dict[str, str]], response_model: type | None) -> Any:
    """One structured call via litellm directly (eval-local — does NOT use the
    production adapter). Native response_format first; JSON-mode fallback."""
    if response_model is None:
        resp = await litellm.acompletion(model=model, messages=messages, temperature=0)
        return resp["choices"][0]["message"]["content"]
    try:
        resp = await litellm.acompletion(model=model, messages=messages, temperature=0,
                                         response_format=response_model)
        return response_model.model_validate_json(_extract_json(resp["choices"][0]["message"]["content"]))
    except Exception:  # noqa: BLE001 — native schema rejected / unsupported → JSON-mode fallback
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        hinted = list(messages)
        hinted[-1] = {**hinted[-1],
                      "content": hinted[-1]["content"]
                      + "\n\nRespond with ONLY a JSON object matching this schema:\n" + schema}
        try:
            resp = await litellm.acompletion(model=model, messages=hinted, temperature=0,
                                             response_format={"type": "json_object"})
        except Exception:  # noqa: BLE001 — provider lacks json_object too
            resp = await litellm.acompletion(model=model, messages=hinted, temperature=0)
        return response_model.model_validate_json(_extract_json(resp["choices"][0]["message"]["content"]))


async def _one_concurrent(req: EvalRequest, *, model: str, counter: TokenCounter) -> ExecResult:
    tokens = counter(model, req.messages)
    try:
        parsed = await _structured_call(model, req.messages, req.response_model)
        return ExecResult(req.key, parsed, tokens, None, "concurrent")
    except Exception as exc:  # noqa: BLE001 — capture, don't abort the batch
        return ExecResult(req.key, None, tokens, str(exc), "concurrent")


async def _run_concurrent(
    requests: list[EvalRequest], *, model: str, concurrency: int, counter: TokenCounter,
) -> dict[str, ExecResult]:
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _guarded(req: EvalRequest) -> ExecResult:
        async with sem:
            return await _one_concurrent(req, model=model, counter=counter)

    results = await asyncio.gather(*(_guarded(r) for r in requests))
    return {r.key: r for r in results}


def _read_content(obj: Any) -> str:
    raw = getattr(obj, "content", None)
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    if isinstance(raw, str):
        return raw
    return str(getattr(obj, "text", "") or raw or "")


async def _run_batch_api(
    requests: list[EvalRequest], *, model: str, counter: TokenCounter,
    poll_interval: float, timeout_s: float,
) -> dict[str, ExecResult]:
    """Provider Batch API path. Raises on any failure so execute() can degrade."""
    provider = _provider_of(model)
    tokens = {r.key: counter(model, r.messages) for r in requests}
    models = {r.key: r.response_model for r in requests}

    lines = []
    for r in requests:
        body: dict[str, Any] = {"model": model, "messages": r.messages, "temperature": 0}
        if r.response_model is not None:
            body["response_format"] = {"type": "json_object"}
            schema = json.dumps(r.response_model.model_json_schema(), ensure_ascii=False)
            body["messages"] = [*r.messages[:-1], {**r.messages[-1],
                "content": r.messages[-1]["content"]
                + "\n\nRespond with ONLY a JSON object matching this schema:\n" + schema}]
        lines.append({"custom_id": r.key, "method": "POST",
                      "url": "/v1/chat/completions", "body": body})

    tmp = Path(tempfile.mkstemp(suffix=".jsonl")[1])
    tmp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    try:
        with tmp.open("rb") as fh:
            file_obj = await litellm.acreate_file(file=fh, purpose="batch", custom_llm_provider=provider)
        batch = await litellm.acreate_batch(
            completion_window="24h", endpoint="/v1/chat/completions",
            input_file_id=file_obj.id, custom_llm_provider=provider)
        elapsed = 0.0
        status = getattr(batch, "status", "")
        while status not in ("completed", "failed", "cancelled", "expired"):
            if elapsed > timeout_s:
                raise TimeoutError(f"batch {batch.id} timed out after {elapsed}s")
            await asyncio.sleep(poll_interval)
            elapsed += max(poll_interval, 0.001)
            batch = await litellm.aretrieve_batch(batch_id=batch.id, custom_llm_provider=provider)
            status = getattr(batch, "status", "")
        if status != "completed":
            raise RuntimeError(f"batch ended with status={status!r}")
        content = await litellm.afile_content(file_id=batch.output_file_id, custom_llm_provider=provider)
    finally:
        tmp.unlink(missing_ok=True)

    results: dict[str, ExecResult] = {}
    for raw in _read_content(content).splitlines():
        raw = raw.strip()
        if not raw:
            continue
        rec = json.loads(raw)
        key = rec.get("custom_id")
        rm = models.get(key)
        try:
            text = rec["response"]["body"]["choices"][0]["message"]["content"]
            parsed = rm.model_validate_json(_extract_json(text)) if rm is not None else text
            results[key] = ExecResult(key, parsed, tokens.get(key), None, "batch_api")
        except Exception as exc:  # noqa: BLE001
            results[key] = ExecResult(key, None, tokens.get(key), str(exc), "batch_api")
    # Any request missing from the output is an error result (don't silently drop).
    for r in requests:
        results.setdefault(r.key, ExecResult(r.key, None, tokens.get(r.key),
                                             "missing from batch output", "batch_api"))
    return results


async def execute(
    requests: list[EvalRequest], *, model: str, backend: str = "auto",
    concurrency: int = 8, count_tokens: TokenCounter | None = None,
    poll_interval: float = 15.0, timeout_s: float = 86400.0,
) -> dict[str, ExecResult]:
    counter = count_tokens or _default_count_tokens
    if not requests:
        return {}
    use_batch = backend == "batch_api" or (
        backend == "auto" and _provider_of(model) in BATCH_CAPABLE_PROVIDERS)
    if use_batch:
        try:
            return await _run_batch_api(requests, model=model, counter=counter,
                                        poll_interval=poll_interval, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 — degrade to concurrent (the user's rule)
            logger.warning("batch_api failed (%s) — degrading to concurrent", exc)
    return await _run_concurrent(requests, model=model, concurrency=concurrency, counter=counter)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_execute.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_execute.py
git add benchmark/agent/execute.py tests/benchmark_agent/test_execute.py
git commit -m "feat(eval): swappable executor — auto Batch API, degrade to concurrent"
```

---

## Task 6: Per-stage replay (render + single execute)

**Files:**
- Create: `backend/benchmark/agent/replay.py`
- Test: `backend/tests/benchmark_agent/test_replay.py`

**Interfaces:**
- Consumes: `load_variant` (Task 4), `execute`/`EvalRequest` (Task 5), `StageSpec`/`CorpusCase`.
- Produces: `render_messages(system, user_template, variables) -> list[dict[str,str]]`; `ReplayOutput` (Shared types); `async replay_stage(spec, version, case, *, model, prompts_dir, backend="auto", count_tokens=None) -> ReplayOutput`. Loads the variant, renders, runs ONE request through `execute`, maps to `ReplayOutput`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_replay.py
import pytest

from benchmark.agent import replay
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.replay import render_messages, replay_stage
from benchmark.agent.stages import get_stage


def test_render_messages():
    msgs = render_messages("SYS", "MSG: {user_message}", {"user_message": "hi"})
    assert msgs == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "MSG: hi"}]


def _seed_variant(tmp_path):
    d = tmp_path / "router"
    d.mkdir(parents=True)
    (d / "v1.yaml").write_text("system: |\n  classify\nuser: |\n  {user_message}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_replay_stage_maps_execresult(tmp_path, monkeypatch):
    _seed_variant(tmp_path)
    from paperhub.models.domain import RoutingDecision
    from benchmark.agent.execute import ExecResult

    async def _fake_execute(requests, **kw):
        d = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.8,
                            reasoning="x", resolved_query="q", response_language="English")
        return {requests[0].key: ExecResult(requests[0].key, d, 33, None, "concurrent")}

    monkeypatch.setattr(replay, "execute", _fake_execute)
    case = CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"},
                      variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False})
    out = await replay_stage(get_stage("router"), "v1", case, model="m", prompts_dir=tmp_path)
    assert out.output["intent"] == "paper_qa" and out.tokens_in == 33 and out.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.replay'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/replay.py
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


def render_messages(system: str, user_template: str, variables: dict[str, Any]) -> list[dict[str, str]]:
    return [{"role": "system", "content": system},
            {"role": "user", "content": user_template.format(**variables)}]


def to_replay_output(spec: StageSpec, res: ExecResult) -> ReplayOutput:
    output = spec.output_summary(res.parsed) if (res.parsed is not None and res.error is None) else {}
    return ReplayOutput(output=output, tokens_in=res.tokens_in, error=res.error)


async def replay_stage(
    spec: StageSpec, version: str, case: CorpusCase, *,
    model: str, prompts_dir: Any, backend: str = "auto",
    count_tokens: TokenCounter | None = None,
) -> ReplayOutput:
    system, user_template = load_variant(spec.key, version, prompts_dir=prompts_dir)
    messages = render_messages(system, user_template, case.variables)
    req = EvalRequest(key=case.case_id, messages=messages, response_model=spec.response_model)
    results = await execute([req], model=model, backend=backend, count_tokens=count_tokens)
    return to_replay_output(spec, results[case.case_id])
```

Create `backend/benchmark/agent/replay_types.py` to hold the dataclass (so `experiment.py` and `replay.py` share it without a cycle):

```python
# backend/benchmark/agent/replay_types.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReplayOutput:
    output: dict[str, Any]
    tokens_in: int | None
    error: str | None = None
```

Re-export it from `replay` for callers that import `from benchmark.agent.replay import ReplayOutput`: add at the end of `replay.py` — nothing needed, the import above already binds the name in `replay`'s namespace.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_replay.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_replay.py
git add benchmark/agent/replay.py benchmark/agent/replay_types.py tests/benchmark_agent/test_replay.py
git commit -m "feat(eval): per-stage replay (render variant + single execute)"
```

---

## Task 7: Grader — deterministic + scalar/pairwise judge

**Files:**
- Create: `backend/benchmark/agent/grade.py`
- Test: `backend/tests/benchmark_agent/test_grade.py`

**Interfaces:**
- Consumes: `StageSpec`, `CorpusCase`, `ReplayOutput` (`replay_types`).
- Produces: `CaseScore` (Shared types); `async score_case(spec, case, replay, rep, *, judge_model=None, judge_fn=None) -> CaseScore` — deterministic first; if `None` and `judge_model` set, call `judge_fn` (default `judge_scalar`), normalized to 0..1; `async judge_scalar(*, request, rubric, output_text, model) -> tuple[float, str]` (1-10 ÷ 10); `async judge_pairwise(*, request, rubric, output_a, output_b, model) -> str` (`'A'|'B'|'tie'`). Temp-0.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_grade.py
import pytest

from benchmark.agent import grade
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.replay_types import ReplayOutput
from benchmark.agent.stages import get_stage


def _case(expect):
    return CorpusCase(case_id="c1", stage="router",
                      variables={"user_message": "q", "enabled_refs_count": 0, "slide_attached": False},
                      expect=expect, rubric="route correctly")


@pytest.mark.asyncio
async def test_deterministic_skips_judge():
    spec = get_stage("router")
    replay = ReplayOutput(output={"intent": "paper_qa"}, tokens_in=88)

    async def _boom(**kw):
        raise AssertionError("judge must not run for deterministic router")

    s = await grade.score_case(spec, _case({"intent": "paper_qa"}), replay, 0, judge_model="x", judge_fn=_boom)
    assert s.score == 1.0 and s.tokens_in == 88 and s.error is None
    miss = await grade.score_case(spec, _case({"intent": "slides"}), replay, 0, judge_model="x", judge_fn=_boom)
    assert miss.score == 0.0


@pytest.mark.asyncio
async def test_errored_replay_scores_zero():
    spec = get_stage("router")
    replay = ReplayOutput(output={}, tokens_in=12, error="provider 500")
    s = await grade.score_case(spec, _case({"intent": "paper_qa"}), replay, 1)
    assert s.score == 0.0 and s.error == "provider 500"


@pytest.mark.asyncio
async def test_judge_scalar_normalizes(monkeypatch):
    async def _fake(**kw):
        return {"choices": [{"message": {"content": '{"score": 8, "rationale": "good"}'}}]}
    monkeypatch.setattr(grade.litellm, "acompletion", _fake)
    score, rationale = await grade.judge_scalar(request="q", rubric="r", output_text="ans", model="m")
    assert score == 0.8 and rationale == "good"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_grade.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.grade'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/grade.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_grade.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_grade.py
git add benchmark/agent/grade.py tests/benchmark_agent/test_grade.py
git commit -m "feat(eval): stage grader — deterministic + scalar/pairwise judge"
```

---

## Task 8: Experiment runner (build → execute → grade → aggregate)

**Files:**
- Create: `backend/benchmark/agent/experiment.py`
- Test: `backend/tests/benchmark_agent/test_experiment.py`

**Interfaces:**
- Consumes: `load_variant` (Task 4), `render_messages`/`to_replay_output` (Task 6), `EvalRequest`/`execute` (Task 5), `score_case`/`CaseScore` (Task 7), `StageSpec`/`CorpusCase`.
- Produces: `ExperimentMeta`, `ExperimentResult` (Shared types); `async run_experiment(spec, version, corpus, *, model, reps=1, judge_model=None, judge_fn=None, prompts_dir, backend="auto", concurrency=8, count_tokens=None, git_commit="unknown", created_at="", corpus_name="", notes="") -> ExperimentResult`; `to_store_payload(result) -> tuple[dict, list[dict]]`. Builds one `EvalRequest` per `(case, rep)` (key `f"{case_id}#{rep}"`), executes the whole list as one batch, grades each, aggregates means.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_experiment.py
import pytest

from paperhub.models.domain import RoutingDecision

from benchmark.agent import experiment, store
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.execute import ExecResult
from benchmark.agent.experiment import run_experiment, to_store_payload
from benchmark.agent.stages import get_stage


def _seed_variant(tmp_path):
    d = tmp_path / "router"; d.mkdir(parents=True)
    (d / "v1.yaml").write_text("system: |\n  classify\nuser: |\n  {user_message}\n", encoding="utf-8")


def _corpus():
    return [
        CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"},
                   variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}),
        CorpusCase(case_id="c2", stage="router", expect={"intent": "slides"},
                   variables={"user_message": "make slides", "enabled_refs_count": 1, "slide_attached": False}),
    ]


@pytest.mark.asyncio
async def test_run_experiment_aggregates_and_persists(tmp_path, monkeypatch):
    _seed_variant(tmp_path)

    async def _fake_execute(requests, **kw):
        d = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                            reasoning="x", resolved_query="q", response_language="English")
        return {r.key: ExecResult(r.key, d, 100, None, "concurrent") for r in requests}

    monkeypatch.setattr(experiment, "execute", _fake_execute)
    spec = get_stage("router")
    result = await run_experiment(spec, "v1", _corpus(), model="m", reps=2, prompts_dir=tmp_path,
                                  count_tokens=lambda m, msgs: 100, git_commit="abc",
                                  created_at="2026-06-29T10:00:00", corpus_name="core")
    assert result.mean_score == 0.5         # c1 hits paper_qa (1.0), c2 misses (0.0)
    assert result.mean_tokens_in == 100.0
    assert len(result.scores) == 4          # 2 cases × 2 reps

    p = tmp_path / "experiments.jsonl"
    meta, rows = to_store_payload(result)
    exp_id = store.record_experiment(p, meta=meta, scores=rows)
    assert store.list_experiments(p)[0]["mean_score"] == 0.5
    assert len(store.get_scores(p, exp_id)) == 4
    assert store.list_experiments(p)[0]["n_cases"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_experiment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.experiment'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/experiment.py
"""Run one experiment: a prompt variant over a corpus, N reps, batched + graded.

Builds every (case, rep) request up front and runs them as ONE batch (the
cost/throughput win), then grades each. Reps give variance so a score delta is
signal, not judge noise (SRS §III-9). Shaped by to_store_payload for the JSONL
store (Task 1).
"""
from __future__ import annotations

import json
from collections.abc import Callable
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
    for (case, rep), req in zip(index, requests):
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
```

(`Callable` import is unused if you don't annotate locally — remove it if ruff flags; the signature uses `TokenCounter`/`JudgeFn` aliases.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_experiment.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check benchmark/agent/experiment.py tests/benchmark_agent/test_experiment.py
git add benchmark/agent/experiment.py tests/benchmark_agent/test_experiment.py
git commit -m "feat(eval): experiment runner — batched build/execute/grade/aggregate"
```

---

## Task 9: Golden-output emission (freeze + propagate)

**Files:**
- Modify: `backend/benchmark/agent/corpus.py` (add `emit_golden`)
- Test: `backend/tests/benchmark_agent/test_golden.py`

**Interfaces:**
- Consumes: `replay_stage` (Task 6).
- Produces: `async emit_golden(spec, version, corpus, *, model, prompts_dir, backend="auto", count_tokens=None) -> list[dict[str, Any]]` — run the FROZEN winning variant over the corpus → `[{case_id, source_run_id, variables, output}]` (the cascade hinge, SRS §III-9: a frozen stage's golden outputs become the next stage's real inputs; wiring into a downstream stage is G2).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_golden.py
import pytest

from benchmark.agent import corpus as corpus_mod
from benchmark.agent.corpus import CorpusCase, emit_golden
from benchmark.agent.replay_types import ReplayOutput
from benchmark.agent.stages import get_stage


@pytest.mark.asyncio
async def test_emit_golden(monkeypatch, tmp_path):
    async def _fake_replay(spec, version, case, **kw):
        return ReplayOutput(output={"intent": "paper_qa", "resolved_query": case.variables["user_message"],
                                    "response_language": "English", "confidence": 0.9}, tokens_in=0)
    monkeypatch.setattr(corpus_mod, "replay_stage", _fake_replay)
    corpus = [CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"}, source_run_id=7,
                         variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False})]
    golden = await emit_golden(get_stage("router"), "v2", corpus, model="m", prompts_dir=tmp_path)
    assert golden == [{
        "case_id": "c1", "source_run_id": 7,
        "variables": {"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False},
        "output": {"intent": "paper_qa", "resolved_query": "what is MHA?",
                   "response_language": "English", "confidence": 0.9}}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_golden.py -v`
Expected: FAIL — `ImportError: cannot import name 'emit_golden'`.

- [ ] **Step 3: Add `emit_golden` to `corpus.py`**

At the top of `corpus.py`, add the import (a module-level import is fine — `replay` imports `corpus`, but `corpus` importing `replay` would cycle; so import lazily inside the function OR import the symbol at module level from `replay` AFTER confirming no cycle). `replay.py` imports `from benchmark.agent.corpus import CorpusCase`, so `corpus` importing `replay` at module top **would** cycle. Import inside the function:

```python
async def emit_golden(
    spec: "StageSpec", version: str, corpus: list[CorpusCase], *,
    model: str, prompts_dir: Any, backend: str = "auto", count_tokens: Any = None,
) -> list[dict[str, Any]]:
    """Run the FROZEN winning variant over the corpus and return its golden
    outputs — the cascade hinge (SRS §III-9): these become the next stage's real
    input set, never synthesized."""
    from benchmark.agent.replay import replay_stage  # local import: avoid corpus<->replay cycle

    out: list[dict[str, Any]] = []
    for case in corpus:
        r = await replay_stage(spec, version, case, model=model, prompts_dir=prompts_dir,
                               backend=backend, count_tokens=count_tokens)
        out.append({"case_id": case.case_id, "source_run_id": case.source_run_id,
                    "variables": case.variables, "output": r.output})
    return out
```

Add the type-only import for `StageSpec` at the top of `corpus.py` (it already imports `get_stage` from `stages`; extend it):

```python
from benchmark.agent.stages import StageSpec, get_stage
```

NOTE for the implementer: the test monkeypatches `corpus_mod.replay_stage`, so bind it at module scope for patchability — instead of the local import, add `from benchmark.agent.replay import replay_stage` **at the BOTTOM of `replay.py`'s import chain**? No — to keep both patchable AND cycle-free, put this at module top of `corpus.py`:

```python
# at the very bottom of corpus.py, after all defs:
from benchmark.agent import replay as _replay  # noqa: E402 — deferred to break the cycle
```

and call `_replay.replay_stage(...)`. The test then patches `corpus_mod.replay_stage`; to honor that, also do `replay_stage = _replay.replay_stage` is fragile. **Simplest that satisfies the test:** import `replay_stage` into `corpus`'s namespace at the bottom of the file and reference the module-level name:

```python
# bottom of corpus.py
from benchmark.agent.replay import replay_stage  # noqa: E402 — bottom import breaks the import cycle
```

and write `emit_golden` to call the module-global `replay_stage(...)` (no local import). The bottom placement runs after `replay` is fully importable (replay imports only `corpus.CorpusCase`, defined above). The test's `monkeypatch.setattr(corpus_mod, "replay_stage", ...)` then works.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_golden.py tests/benchmark_agent/test_corpus.py -v`
Expected: PASS (3 passed total).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check benchmark/agent/corpus.py tests/benchmark_agent/test_golden.py
git add benchmark/agent/corpus.py tests/benchmark_agent/test_golden.py
git commit -m "feat(eval): emit_golden — freeze a variant + propagate golden outputs"
```

---

## Task 10: CLI (`harvest | run | compare | list | golden`) + launcher

**Files:**
- Create: `backend/benchmark/agent/cli.py`
- Create: `backend/scripts/run-eval.ps1`
- Test: `backend/tests/benchmark_agent/test_cli.py`

**Interfaces:**
- Produces: `def main(argv: list[str] | None = None) -> int`. Verbs:
  - `harvest --db <path> --stage router --out <jsonl> [--run-ids 1,2] [--limit N]`
  - `run --stage router --version v1 --corpus <jsonl> --model <m> [--reps N] [--judge-model m] [--store <jsonl>] [--prompts-dir <dir>] [--backend auto|concurrent|batch_api] [--env <.env>] [--notes "…"]`
  - `golden --stage router --version v2 --corpus <jsonl> --model <m> --out <jsonl> [--prompts-dir <dir>] [--backend …] [--env <.env>]`
  - `compare --store <jsonl> --a <id> --b <id>`
  - `list --store <jsonl> [--stage router]`
- Defaults: `--store benchmark/agent/results/experiments.jsonl`, `--prompts-dir benchmark/agent/prompts`, `--backend auto`. `run`/`golden` resolve git commit + timestamp. Tests inject a fake via `_run_experiment`/`_emit_golden` seams (monkeypatched) — no network.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_cli.py
import pytest

from benchmark.agent import cli, store
from benchmark.agent.experiment import ExperimentMeta, ExperimentResult
from benchmark.agent.grade import CaseScore


def _write_corpus(path):
    path.write_text(
        '{"case_id":"c1","stage":"router","variables":{"user_message":"q1","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"},"rubric":"","source_run_id":null,"observed":null}\n'
        '{"case_id":"c2","stage":"router","variables":{"user_message":"q2","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"slides"},"rubric":"","source_run_id":null,"observed":null}\n',
        encoding="utf-8")


def test_run_then_list(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "router.core.jsonl"
    _write_corpus(corpus)
    db = tmp_path / "experiments.jsonl"

    async def _fake_run_experiment(spec, version, cases, **kw):
        scores = [CaseScore("c1", 0, 1.0, 100, "hit", {"intent": "paper_qa"}),
                  CaseScore("c2", 0, 0.0, 100, "miss", {"intent": "paper_qa"})]
        meta = ExperimentMeta(git_commit="abc", stage="router", prompt_version="router/v1",
                              model=kw["model"], corpus=kw.get("corpus_name", ""), reps=1,
                              created_at=kw.get("created_at", ""))
        return ExperimentResult(meta=meta, scores=scores, mean_score=0.5, mean_tokens_in=100.0)

    monkeypatch.setattr(cli, "_run_experiment", _fake_run_experiment)
    rc = cli.main(["run", "--stage", "router", "--version", "v1", "--corpus", str(corpus),
                   "--model", "gemini/gemini-2.5-flash", "--store", str(db), "--prompts-dir", str(tmp_path)])
    assert rc == 0
    exps = store.list_experiments(db, stage="router")
    assert len(exps) == 1 and exps[0]["mean_score"] == 0.5

    rc = cli.main(["list", "--store", str(db), "--stage", "router"])
    assert rc == 0 and "router/v1" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.cli'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/cli.py
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
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmark.agent import corpus as corpus_mod
from benchmark.agent import store
from benchmark.agent.experiment import run_experiment as _run_experiment
from benchmark.agent.experiment import to_store_payload
from benchmark.agent.stages import STAGE_REGISTRY, get_stage

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="benchmark.agent.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    stages = sorted(STAGE_REGISTRY)
    backends = ["auto", "concurrent", "batch_api"]

    h = sub.add_parser("harvest", help="build a per-stage corpus from the trace DB")
    h.add_argument("--db", required=True); h.add_argument("--stage", required=True, choices=stages)
    h.add_argument("--out", required=True); h.add_argument("--run-ids", default="")
    h.add_argument("--limit", type=int, default=200); h.set_defaults(fn=_cmd_harvest)

    r = sub.add_parser("run", help="run a variant over a corpus + persist an experiment")
    r.add_argument("--stage", required=True, choices=stages); r.add_argument("--version", required=True)
    r.add_argument("--corpus", required=True); r.add_argument("--model", required=True)
    r.add_argument("--reps", type=int, default=1); r.add_argument("--judge-model", default="")
    r.add_argument("--store", default=DEFAULT_STORE); r.add_argument("--prompts-dir", default=DEFAULT_PROMPTS)
    r.add_argument("--backend", default="auto", choices=backends); r.add_argument("--env", default=".env")
    r.add_argument("--notes", default=""); r.set_defaults(fn=_cmd_run)

    g = sub.add_parser("golden", help="emit a frozen variant's golden outputs")
    g.add_argument("--stage", required=True, choices=stages); g.add_argument("--version", required=True)
    g.add_argument("--corpus", required=True); g.add_argument("--model", required=True)
    g.add_argument("--out", required=True); g.add_argument("--prompts-dir", default=DEFAULT_PROMPTS)
    g.add_argument("--backend", default="auto", choices=backends); g.add_argument("--env", default=".env")
    g.set_defaults(fn=_cmd_golden)

    c = sub.add_parser("compare", help="diff two experiments")
    c.add_argument("--store", default=DEFAULT_STORE); c.add_argument("--a", type=int, required=True)
    c.add_argument("--b", type=int, required=True); c.set_defaults(fn=_cmd_compare)

    li = sub.add_parser("list", help="list experiments")
    li.add_argument("--store", default=DEFAULT_STORE); li.add_argument("--stage", default="")
    li.set_defaults(fn=_cmd_list)

    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add the launcher**

Create `backend/scripts/run-eval.ps1`:

```powershell
# Launcher for the per-stage prompt-eval CLI (SRS §III-9). In-process — no live
# backend needed; needs an LLM API key in backend/.env. Examples:
#   scripts/run-eval.ps1 run --stage router --version v1 --corpus benchmark/agent/corpus/router.core.jsonl --model gemini/gemini-2.5-flash
#   scripts/run-eval.ps1 list --stage router
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $EvalArgs)
uv run python -m benchmark.agent.cli @EvalArgs
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_cli.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_cli.py
git add benchmark/agent/cli.py scripts/run-eval.ps1 tests/benchmark_agent/test_cli.py
git commit -m "feat(eval): paperhub-eval CLI (harvest/run/compare/list/golden) + launcher"
```

---

## Task 11: Config-driven sweep — variants × test-set buckets

**Files:**
- Create: `backend/benchmark/agent/eval_config.py`
- Create: `backend/benchmark/agent/sweep.py`
- Modify: `backend/benchmark/agent/cli.py` (add the `sweep` verb)
- Test: `backend/tests/benchmark_agent/test_sweep.py`

**Interfaces:**
- Produces: `eval_config.py`: `TestSet`, `EvalConfig` (Shared types), `load_eval_config(path) -> EvalConfig`. `sweep.py`: `SweepCell`, `async run_sweep(cfg, *, store_path, git_commit, created_at, count_tokens=None) -> list[SweepCell]`, `matrix_report(cfg, cells) -> str`. `run_sweep` builds the whole grid (all variants × buckets × cases) and runs each cell's experiment (one batch per cell via `run_experiment`), persists each to JSONL, returns cells. `cli.py`: `sweep --config <toml> [--out <md>] [--env <.env>]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_sweep.py
import pytest

from benchmark.agent import store, sweep
from benchmark.agent.eval_config import load_eval_config
from benchmark.agent.experiment import ExperimentMeta, ExperimentResult
from benchmark.agent.grade import CaseScore
from benchmark.agent.sweep import matrix_report, run_sweep


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_eval_config(tmp_path):
    core = tmp_path / "core.jsonl"
    _write(core, ['{"case_id":"a","stage":"router","variables":{"user_message":"q","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"}}'])
    toml = tmp_path / "router.eval.toml"
    toml.write_text('[eval]\nstage="router"\nmodel="m"\nreps=2\nbackend="auto"\nvariants=["v1","v2"]\n'
                    f'store="{(tmp_path / "e.jsonl").as_posix()}"\nprompts_dir="{tmp_path.as_posix()}"\n\n'
                    f'[[testsets]]\nname="core"\ncorpus="{core.as_posix()}"\n', encoding="utf-8")
    cfg = load_eval_config(toml)
    assert cfg.stage == "router" and cfg.reps == 2 and cfg.backend == "auto"
    assert cfg.variants == ["v1", "v2"] and cfg.testsets[0].name == "core"


@pytest.mark.asyncio
async def test_run_sweep_grid_and_matrix(tmp_path, monkeypatch):
    core = tmp_path / "core.jsonl"; reg = tmp_path / "regression.jsonl"
    _write(core, ['{"case_id":"a","stage":"router","variables":{"user_message":"q","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"}}'])
    _write(reg, ['{"case_id":"b","stage":"router","variables":{"user_message":"d","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"slides"}}'])
    toml = tmp_path / "router.eval.toml"
    toml.write_text('[eval]\nstage="router"\nmodel="m"\nreps=1\nvariants=["v1","v2"]\n'
                    f'store="{(tmp_path / "e.jsonl").as_posix()}"\nprompts_dir="{tmp_path.as_posix()}"\n\n'
                    f'[[testsets]]\nname="core"\ncorpus="{core.as_posix()}"\n\n'
                    f'[[testsets]]\nname="regression"\ncorpus="{reg.as_posix()}"\n', encoding="utf-8")
    cfg = load_eval_config(toml)

    async def _fake_run_experiment(spec, version, cases, **kw):
        # v1 -> always paper_qa; v2 -> always slides. core expects paper_qa, regression expects slides.
        intent = "slides" if version == "v2" else "paper_qa"
        scores = [CaseScore(c.case_id, 0, 1.0 if c.expect["intent"] == intent else 0.0, 100, "", {}) for c in cases]
        mean = sum(s.score for s in scores) / len(scores)
        meta = ExperimentMeta("abc", "router", f"router/{version}", "m", kw.get("corpus_name", ""), 1, "")
        return ExperimentResult(meta=meta, scores=scores, mean_score=mean, mean_tokens_in=100.0)

    monkeypatch.setattr(sweep, "run_experiment", _fake_run_experiment)
    cells = await run_sweep(cfg, store_path=cfg.store, git_commit="abc", created_at="2026-06-29T10:00:00",
                            count_tokens=lambda m, msgs: 100)
    assert len(cells) == 4 and len(store.list_experiments(cfg.store)) == 4
    md = matrix_report(cfg, cells)
    assert "router/v1" in md and "router/v2" in md and "core" in md and "regression" in md
    assert "-1.00" in md and "⚠" in md   # core regressed v1->v2
    assert "+1.00" in md                  # regression improved v1->v2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.eval_config'`.

- [ ] **Step 3a: Write the config loader**

```python
# backend/benchmark/agent/eval_config.py
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
```

- [ ] **Step 3b: Write the sweep orchestrator + matrix report**

```python
# backend/benchmark/agent/sweep.py
"""Run the variants × test-sets grid and report it (SRS §III-9).

Each cell (variant, bucket) is its own persisted experiment, keyed to
git_commit/stage/version/model + the bucket name. The matrix report puts variants
in columns and buckets in rows, with a Δ-vs-baseline column that flags any bucket
REGRESSION (⚠) — the "fix A breaks B/C" signal, made visible.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
```

- [ ] **Step 3c: Add the `sweep` verb to `cli.py`**

Add imports next to the other `benchmark.agent` imports in `cli.py`:

```python
from benchmark.agent.eval_config import load_eval_config
from benchmark.agent.sweep import matrix_report, run_sweep
```

Add the handler (next to `_cmd_run`):

```python
def _cmd_sweep(args: argparse.Namespace) -> int:
    _load_env(args.env)
    cfg = load_eval_config(args.config)
    cells = asyncio.run(run_sweep(cfg, store_path=cfg.store, git_commit=_git_commit(),
                                  created_at=datetime.now().isoformat(timespec="seconds"),
                                  count_tokens=_token_counter))
    report = matrix_report(cfg, cells)
    out = args.out or f"benchmark/agent/results/{cfg.stage}-sweep-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWrote {out}")
    return 0
```

Register the subparser (between the `run` and `golden` parsers in `main`):

```python
    sw = sub.add_parser("sweep", help="run a variants x test-sets grid from a TOML config")
    sw.add_argument("--config", required=True); sw.add_argument("--out", default="")
    sw.add_argument("--env", default=".env"); sw.set_defaults(fn=_cmd_sweep)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_sweep.py tests/benchmark_agent/test_cli.py -v`
Expected: PASS (test_sweep 2 passed; test_cli still green).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_sweep.py
git add benchmark/agent/eval_config.py benchmark/agent/sweep.py benchmark/agent/cli.py tests/benchmark_agent/test_sweep.py
git commit -m "feat(eval): config-driven sweep — variants x test-set buckets + matrix report"
```

---

## Task 12: Router corpus buckets, prompt-variant seed, sweep config, README, real-API gate

**Files:**
- Create: `backend/benchmark/agent/corpus/router.core.jsonl`, `router.regression.jsonl`, `router.edge.jsonl`
- Create: `backend/benchmark/agent/prompts/router/v1.yaml` (copy of the shipped prompt)
- Create: `backend/benchmark/agent/router.eval.toml`
- Create: `backend/benchmark/agent/README.md`

**Interfaces:** none (data + config + docs + the one-time real-API verification).

- [ ] **Step 1: Write the `core` bucket (one clean case per intent)**

Create `backend/benchmark/agent/corpus/router.core.jsonl`:

```jsonl
{"case_id": "core-qa-mha", "stage": "router", "variables": {"user_message": "How does multi-head attention differ from single-head?", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_qa"}, "rubric": "content question about an enabled paper -> paper_qa", "source_run_id": null, "observed": null}
{"case_id": "core-search-named", "stage": "router", "variables": {"user_message": "Find the paper 'Attention Is All You Need'", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "paper_search"}, "rubric": "resolve a named paper -> paper_search", "source_run_id": null, "observed": null}
{"case_id": "core-suggest", "stage": "router", "variables": {"user_message": "Recommend a few papers on mixture-of-experts routing", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_suggest"}, "rubric": "topic recommendation -> paper_suggest", "source_run_id": null, "observed": null}
{"case_id": "core-slides", "stage": "router", "variables": {"user_message": "Make a 10-slide deck about these papers", "enabled_refs_count": 2, "slide_attached": false}, "expect": {"intent": "slides"}, "rubric": "deck command -> slides", "source_run_id": null, "observed": null}
{"case_id": "core-libstats", "stage": "router", "variables": {"user_message": "List my papers about diffusion models", "enabled_refs_count": 3, "slide_attached": false}, "expect": {"intent": "library_stats"}, "rubric": "library-scoped possessive listing -> library_stats", "source_run_id": null, "observed": null}
{"case_id": "core-memory", "stage": "router", "variables": {"user_message": "Remember that I always want answers in Traditional Chinese", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "memory"}, "rubric": "explicit remember -> memory", "source_run_id": null, "observed": null}
{"case_id": "core-chitchat", "stage": "router", "variables": {"user_message": "Hi there, how are you?", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "chitchat"}, "rubric": "greeting -> chitchat", "source_run_id": null, "observed": null}
```

- [ ] **Step 2: Write the `regression` bucket (side-effect guards)**

Create `backend/benchmark/agent/corpus/router.regression.jsonl`:

```jsonl
{"case_id": "reg-qa-noref-to-search", "stage": "router", "variables": {"user_message": "What does the transformer paper say about positional encodings?", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "paper_search"}, "rubric": "content question with NO refs attached -> router rewrites to paper_search", "source_run_id": null, "observed": null}
{"case_id": "reg-possessive-libstats", "stage": "router", "variables": {"user_message": "Show me the papers I already have on transformers", "enabled_refs_count": 2, "slide_attached": false}, "expect": {"intent": "library_stats"}, "rubric": "possessive 'papers I have' -> library_stats, NOT paper_suggest", "source_run_id": null, "observed": null}
{"case_id": "reg-language-stable", "stage": "router", "variables": {"user_message": "多頭注意力和單頭注意力有什麼不同?", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_qa"}, "rubric": "same content question in Traditional Chinese -> intent stays paper_qa", "source_run_id": null, "observed": null}
{"case_id": "reg-named-search-not-suggest", "stage": "router", "variables": {"user_message": "Search for the BERT paper", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "paper_search"}, "rubric": "named paper -> paper_search, NOT paper_suggest", "source_run_id": null, "observed": null}
{"case_id": "reg-onscreen-deck-qa", "stage": "router", "variables": {"user_message": "What is the second slide about?", "enabled_refs_count": 1, "slide_attached": true}, "expect": {"intent": "paper_qa"}, "rubric": "a QUESTION about the on-screen deck -> paper_qa (deck command vs question, v2.29)", "source_run_id": null, "observed": null}
```

- [ ] **Step 3: Write the `edge` bucket (ambiguous / short / anaphora)**

Create `backend/benchmark/agent/corpus/router.edge.jsonl`:

```jsonl
{"case_id": "edge-bare-followup-suggest", "stage": "router", "variables": {"user_message": "推薦幾篇", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_suggest"}, "rubric": "bare anaphoric 'recommend a few' -> paper_suggest", "source_run_id": null, "observed": null}
{"case_id": "edge-one-word-slides", "stage": "router", "variables": {"user_message": "slides", "enabled_refs_count": 2, "slide_attached": false}, "expect": {"intent": "slides"}, "rubric": "one-word deck command -> slides", "source_run_id": null, "observed": null}
{"case_id": "edge-anaphora-qa", "stage": "router", "variables": {"user_message": "tell me more about this one", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_qa"}, "rubric": "anaphoric 'this one' with an enabled ref -> paper_qa", "source_run_id": null, "observed": null}
{"case_id": "edge-unresolvable-clarify", "stage": "router", "variables": {"user_message": "do the thing", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "clarify"}, "rubric": "unresolvable with no refs and no antecedent -> clarify", "source_run_id": null, "observed": null}
```

- [ ] **Step 4: Seed the baseline prompt variant (COPY — do not edit the registry)**

Copy the shipped router prompt into the eval folder verbatim (this reads production, writes eval-only — it does NOT modify deploy code):

```bash
cd backend
mkdir -p benchmark/agent/prompts/router
cp src/paperhub/llm/prompts/router_v1.yaml benchmark/agent/prompts/router/v1.yaml
```

Verify it has top-level `system:` and `user:` block scalars (the loader needs both). Do **not** modify `src/paperhub/llm/prompts/router_v1.yaml`.

- [ ] **Step 5: Write the sweep config**

Create `backend/benchmark/agent/router.eval.toml`:

```toml
# Per-agent eval sweep config (SRS §III-9): variants × test-set buckets.
# Add a variant: write benchmark/agent/prompts/router/v2.yaml, then append "v2".
# Add a bucket: add a [[testsets]] block (e.g. the harvested production failures).
[eval]
stage       = "router"
model       = "gemini/gemini-2.5-flash"
reps        = 3                       # variance — a delta within noise is not a win
backend     = "auto"                  # provider Batch API where available, else concurrent
store       = "benchmark/agent/results/experiments.jsonl"
prompts_dir = "benchmark/agent/prompts"
# judge_model is only used for stages WITHOUT a deterministic score; the router
# is graded by exact intent match, so it is omitted.
variants    = ["v1"]                  # add "v2" once prompts/router/v2.yaml exists

[[testsets]]
name   = "core"
corpus = "benchmark/agent/corpus/router.core.jsonl"

[[testsets]]
name   = "regression"
corpus = "benchmark/agent/corpus/router.regression.jsonl"

[[testsets]]
name   = "edge"
corpus = "benchmark/agent/corpus/router.edge.jsonl"
```

- [ ] **Step 6: Write the README**

Create `backend/benchmark/agent/README.md`:

```markdown
# Per-stage agent prompt evaluation (SRS §III-9, Plan G1)

Evaluate ONE agent prompt at a time, precisely, on real recorded inputs — replay
the stage with a prompt *variant* (a YAML file in this folder), score its output
(quality + token count), and persist each run as a comparable JSONL experiment.

**Isolation:** this tool touches **no deploy code**. Variants live here, not in
`src/.../llm/prompts/`; it calls the LLM via litellm directly. Adopting a winning
variant into the production registry is a separate, deliberate step you take
AFTER a sweep — never automated. In-process: **no live backend needed**, only an
LLM API key in `backend/.env`.

## The loop (run from `backend/`)

```powershell
# 1. (optional) harvest REAL inputs from the trace DB into a new bucket
scripts/run-eval.ps1 harvest --db workspace/paperhub.db --stage router `
  --out benchmark/agent/corpus/router.harvest.jsonl

# 2. write a new variant: benchmark/agent/prompts/router/v2.yaml (system:/user:),
#    add "v2" to router.eval.toml's variants, then sweep the whole grid:
scripts/run-eval.ps1 sweep --config benchmark/agent/router.eval.toml
```

The sweep prints + writes a matrix report — variants as columns, buckets as rows,
with a Δ-vs-baseline column flagging any **regression** (⚠):

```
# Eval sweep: router
- model: gemini-2.5-flash · reps: 3 · backend: auto · variants: router/v1, router/v2
| test set   | router/v1 (score/tok) | router/v2 (score/tok) | Δ router/v2 vs router/v1 |
|---|---|---|---|
| core       | 0.86 / 1180           | 0.95 / 910            | +0.09                    |
| regression | 1.00 / 1180           | 0.80 / 910            | -0.20 ⚠                  |
| edge       | 0.50 / 1180           | 0.75 / 910            | +0.25                    |
```

That ⚠ is the point: v2 is more concise and fixes edge cases but breaks a
behaviour the regression bucket guards — adopt nothing until it's clean.

## The primitives (what `sweep` orchestrates)

```powershell
scripts/run-eval.ps1 run --stage router --version v1 `
  --corpus benchmark/agent/corpus/router.core.jsonl --model gemini/gemini-2.5-flash
scripts/run-eval.ps1 list --stage router
scripts/run-eval.ps1 compare --a 1 --b 2
# freeze the winner -> emit golden outputs (the NEXT stage's real inputs)
scripts/run-eval.ps1 golden --stage router --version v2 `
  --corpus benchmark/agent/corpus/router.core.jsonl `
  --model gemini/gemini-2.5-flash --out benchmark/agent/corpus/router.golden.jsonl
```

## Concepts

- **Variant** — a prompt version = `prompts/<stage>/<version>.yaml`, browsable +
  editable. `v1` is seeded from the shipped registry prompt (the baseline).
- **Test-set buckets** — `core` (target), `regression` (side-effect guard),
  `edge` (ambiguous/short), `harvest` (real production failures, promoted in).
- **Two scores** — output quality (`mean_score`, 0..1; deterministic intent match
  for the router, judge otherwise) + prompt quality (`mean_tokens_in`).
- **Reps** — N times per case for variance, so a delta is signal not noise.
- **Backend** — `auto` uses the provider Batch API where available (~50% cheaper)
  and degrades to concurrent requests otherwise. `--no-…`: pass `--backend concurrent`.
- **Freeze + propagate** — a frozen winner's golden outputs become the next
  stage's real input set; the cascade rolls down from the router.
- **Adopt** — to ship a winning variant, copy its YAML into
  `src/.../llm/prompts/` as a new `_vN.yaml` and switch the call site. A separate
  `writing-agent-prompts` step — the eval never does it for you.

`results/` + `*.harvest.jsonl` are gitignored; buckets, variants, and
`router.eval.toml` are committed. See SRS §III-9 + `writing-agent-prompts`.
```

- [ ] **Step 7: Run the new test suite + commit data/config/docs**

Run: `cd backend; uv run pytest tests/benchmark_agent/ -v; uv run ruff check tests/benchmark_agent`
Expected: all green; ruff clean.

```bash
cd backend
git add benchmark/agent/corpus/router.core.jsonl benchmark/agent/corpus/router.regression.jsonl \
        benchmark/agent/corpus/router.edge.jsonl benchmark/agent/prompts/router/v1.yaml \
        benchmark/agent/router.eval.toml benchmark/agent/README.md
git commit -m "docs(eval): router corpora buckets + baseline variant + sweep config + README"
```

- [ ] **Step 8: Real-API pilot gate (run ONCE — the plan-phase verification)**

Needs an LLM API key in `backend/.env` (NOT the `:8000` backend — replay is in-process). For `gemini/gemini-2.5-flash` the executor degrades to concurrent automatically (Gemini AI Studio isn't in the batch allowlist) — that's expected.

```powershell
cd backend
scripts/run-eval.ps1 sweep --config benchmark/agent/router.eval.toml
scripts/run-eval.ps1 list --stage router
```

Confirm:
1. The sweep prints a matrix report (rows core/regression/edge, column router/v1) and writes it under `benchmark/agent/results/`.
2. `list --stage router` shows **three** experiments (one per bucket), keyed to the git commit + `router/v1`, in `benchmark/agent/results/experiments.jsonl`.
3. Open the JSONL: each line has nested `scores` with per-case `score` (1.0 correct / 0.0 miss) + `tokens_in`. Any miss is a genuinely-debatable router behaviour (a real finding to feed a `router/v2.yaml` rewrite), not a harness bug.

- [ ] **Step 9: Full backend quality gates (plan-phase completion)**

Run: `cd backend; uv run pytest -q; uv run ruff check src tests; uv run mypy src`
Expected: all green. **Confirm `git diff --stat main...HEAD` shows ZERO `src/` changes** (the isolation rule).

---

## Self-Review

**Spec coverage (SRS §III-9 → tasks):**
- "Real-input corpus, never synthesized; failed-run promotion" → Task 3 (`harvest`), Task 12 (buckets). ✓
- "Per-stage replay, in-process, prompt is the only variable" → Task 6 (`replay_stage` + `render_messages`), variants from Task 4's folder. ✓
- "≥2 variants × corpus × judge; N-run variance; pairwise" → Task 8 (`reps`), Task 7 (`judge_scalar`/`judge_pairwise`); variant-vs-variant = the sweep (Task 11) or `run`+`compare` (Task 10). ✓
- "Configurable prompt-set × test-sets (human/Claude-editable)" → Task 4 (YAML-folder variants) + Task 11 (`eval_config` + `sweep` + matrix) over Task 12's buckets. ✓
- "Two scores: output quality + token count" → Task 7 (`score`) + Task 5 (`tokens_in`), aggregated Task 8, stored Task 1. ✓
- "Freeze + propagate golden outputs" → Task 9 (`emit_golden`), CLI `golden` (Task 10). ✓
- "Local-first JSONL store keyed to {git_commit, stage, prompt_version, model}; CLI harvest|run|sweep|compare|list|golden" → Task 1 + Tasks 10/11. ✓
- "Cost-aware batch, auto with degrade" → Task 5 (`execute`, auto Batch API → concurrent), batched per cell in Tasks 8/11. ✓
- "ZERO deploy-code impact" → no `src/` file is created/modified by any task; Task 5/7 call litellm directly; Task 4 keeps variants in an eval folder; Task 12 step 4 *copies* (not edits) the registry prompt; Task 12 step 9 asserts an empty `src/` diff. ✓
- Acceptance I-8 #7 (replay in isolation, judged variant-vs-baseline with variance, persisted experiment keyed to commit/stage/version/model) → Tasks 4–11. ✓
- Deferred by design (noted): downstream stages beyond router; promoting a winner to the registry (the post-sweep human decision); the optional LangSmith export.

**Placeholder scan:** every code step has complete runnable code; tests assert concrete values; no "TBD"/"handle edge cases"/"similar to Task N". ✓

**Type consistency:** `CorpusCase`, `StageSpec`, `EvalRequest`/`ExecResult`, `ReplayOutput` (in `replay_types`), `CaseScore`, `ExperimentMeta`/`ExperimentResult`, `EvalConfig`/`TestSet`/`SweepCell` field names match across Tasks 1–11; `execute`/`replay_stage`/`score_case`/`run_experiment`/`to_store_payload`/`record_experiment`/`run_sweep`/`matrix_report`/`emit_golden` signatures are used identically by their callers; `prompts_dir`, `backend`, and the JSONL `store` path thread consistently from the CLI/config down to `execute`. ✓
