# Plan G1 — Agent Chain Eval (cascade per-stage prompt evaluation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first engine that evaluates one agent prompt at a time, *precisely*, on real recorded inputs — replay a stage with a chosen prompt version, score its output (quality + token count), and persist each run as a comparable experiment — and prove the loop end-to-end on the **Router** stage.

**Architecture:** A new `backend/benchmark/agent/` package. Per-stage replay reuses the production `LiteLlmAdapter` (which already renders a prompt from a `slot` + `variables`), so replaying a stage with a different prompt version is just a different `slot` over the *same recorded input state* (`tool_calls.args_redacted_json`). Scores land in a gitignored SQLite `eval.db` keyed to `{git_commit, stage, prompt_version, model}`, queryable for `compare`/`list`. This is Phase-1 of SRS §III-9 (the per-stage engine + router pilot); rolling the cascade to downstream stages and the optional LangSmith export are follow-on plans (G2/G3).

**Tech Stack:** Python 3.11, `uv`, `litellm` (already a dep), stdlib `sqlite3` (sync — the harness is a sync CLI like `benchmark/runner.py`), `pydantic`, `pytest` + `pytest-asyncio`. No new third-party dependency.

## Global Constraints

- **Python tooling:** `uv` only — never `pip`/system python. From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`.
- **mypy/ruff scope:** `mypy --strict` runs on `src` only; the one `src` change in this plan (Task 4, `LiteLlmAdapter.build_messages`) MUST pass it. `ruff check src tests` covers the new test files under `tests/benchmark_agent/` — keep them lint-clean. The `benchmark/agent/` package itself is outside both gates (same as the existing `benchmark/` code) but write it typed and clean anyway.
- **Test discipline (TDD):** every task is failing-test-first → minimal impl → green → commit. **No unit test may call a real LLM/network** — stub the adapter and monkeypatch `litellm`. The real-API run happens once, at the end (Task 9), per the project's "pytest is necessary-but-insufficient" rule.
- **Per-task test scope:** run only the new test file(s) + targeted `ruff`/`mypy`, not the full suite (full suite only at plan-phase completion — it is expensive).
- **Commits:** Conventional Commits — `feat(eval): …`, `test(eval): …`, `docs(eval): …`. Body wraps at 72 cols. Co-author trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Shell:** PowerShell on Windows (`;` to chain, `$LASTEXITCODE`). Bash tool available for POSIX scripts.
- **Never record the rendered prompt** in any persisted artifact — store the `{placeholder}` input state (the `variables` dict), per the record principle. Replay re-renders from state.
- **Restricted ops:** local commits/branches are fine to proceed on; `git push`/PR/merge need explicit per-instance approval.

---

## File Structure

**New package — `backend/benchmark/agent/`** (each file one responsibility):

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker (empty). |
| `store.py` | `eval.db` schema + sync `sqlite3` access: `connect`, `record_experiment`, `list_experiments`, `get_scores`, `compare`. No knowledge of stages/LLMs. |
| `stages.py` | `StageSpec` (data-driven: slot base, trace identifiers, response model, and the three per-stage callables) + `STAGE_REGISTRY` with the **router** entry. |
| `corpus.py` | `CorpusCase` + `harvest()` (read `tool_calls` for a stage → cases) + `load_corpus`/`save_corpus` (JSONL). |
| `replay.py` | `ReplayOutput` + `replay_stage()` — render via the adapter, count prompt tokens, call the stage's slot/version, return the structured output. |
| `grade.py` | `CaseScore` + `score_case()` (deterministic first, scalar-judge fallback) + `judge_scalar`/`judge_pairwise` (temp-0, normalized 0..1). |
| `experiment.py` | `ExperimentMeta`/`ExperimentResult` + `run_experiment()` — corpus × reps → replay → grade → aggregate. |
| `eval_config.py` | TOML loader for a per-agent eval config: `[eval]` (stage, model, reps, judge_model, store, variants) + `[[testsets]]` (name, corpus). The human/Claude-editable declaration of "which prompts × which test sets." |
| `sweep.py` | Orchestrate the variants × test-sets grid over `run_experiment`, persist every cell as its own experiment, and render the matrix Markdown report (Δ vs baseline + ⚠ regression marker). |
| `cli.py` | `argparse` subcommands `harvest | run | sweep | compare | list | golden`; wires everything; resolves git commit + timestamp. |
| `corpus/router.{core,regression,edge}.jsonl` | Committed hand-labeled router corpora split by bucket (target behaviour / side-effect guard / ambiguous). Harvested real failures land in the gitignored `router.harvest.jsonl`. |
| `router.eval.toml` | Committed sweep config for the router: `variants` (`["v1"]`; add `"v2"` when you write it) × the three committed test-set buckets. |
| `README.md` | The loop, the CLI verbs, the cascade methodology pointer to SRS §III-9. |

**One `src` change:** `backend/src/paperhub/llm/litellm_adapter.py` — add a thin public `build_messages()` (Task 4) so replay can count prompt tokens against the exact rendered messages without re-implementing rendering.

**New tests — `backend/tests/benchmark_agent/`** (`__init__.py` + one `test_*.py` per package file).

**Config:** append `eval.db` and `agent/corpus/*.harvest.jsonl` to `backend/benchmark/.gitignore` (Task 1). Add `backend/scripts/run-eval.ps1` launcher (Task 8).

### Shared types (defined once, referenced by later tasks)

```python
# stages.py
@dataclass(frozen=True)
class StageSpec:
    key: str                                   # 'router'
    slot_base: str                             # 'router'  → slot = f"{slot_base}/{version}"
    trace_agent: str                           # tool_calls.agent value, e.g. 'router'
    trace_tool: str                            # tool_calls.tool value,  e.g. 'classify'
    response_model: type[BaseModel] | None     # RoutingDecision for structured stages
    variables_from_args: Callable[[dict[str, Any]], dict[str, Any]]
    output_summary: Callable[[Any], dict[str, Any]]
    deterministic_score: Callable[[dict[str, Any], dict[str, Any]], float | None]

# corpus.py
@dataclass
class CorpusCase:
    case_id: str
    stage: str
    variables: dict[str, Any]      # the stage's input state (template vars)
    expect: dict[str, Any]         # reference labels, e.g. {"intent": "paper_qa"}
    rubric: str = ""
    source_run_id: int | None = None
    observed: dict[str, Any] | None = None   # the recorded prod output (label aid)

# replay.py
@dataclass
class ReplayOutput:
    output: dict[str, Any]         # spec.output_summary(...)
    tokens_in: int | None
    error: str | None = None

# grade.py
@dataclass
class CaseScore:
    case_id: str
    rep: int
    score: float | None            # 0..1 (deterministic 0/1, or judge 1-10 ÷ 10)
    tokens_in: int | None
    rationale: str
    output: dict[str, Any]
    error: str | None = None

# experiment.py
@dataclass
class ExperimentMeta:
    git_commit: str
    stage: str
    prompt_version: str            # 'router/v1'
    model: str
    corpus: str                    # corpus file basename
    reps: int
    created_at: str                # ISO8601
    notes: str = ""

@dataclass
class ExperimentResult:
    meta: ExperimentMeta
    scores: list[CaseScore]
    mean_score: float | None
    mean_tokens_in: float | None
```

---

## Task 1: `eval.db` experiment store

**Files:**
- Create: `backend/benchmark/agent/__init__.py` (empty)
- Create: `backend/benchmark/agent/store.py`
- Create: `backend/tests/benchmark_agent/__init__.py` (empty)
- Test: `backend/tests/benchmark_agent/test_store.py`
- Modify: `backend/benchmark/.gitignore` (append `eval.db` and `agent/corpus/*.harvest.jsonl`)

**Interfaces:**
- Produces: `connect(path: str | Path) -> sqlite3.Connection`; `record_experiment(conn, *, meta: dict[str, Any], scores: list[dict[str, Any]]) -> int`; `list_experiments(conn, stage: str | None = None) -> list[dict[str, Any]]`; `get_scores(conn, experiment_id: int) -> list[dict[str, Any]]`; `compare(conn, exp_a: int, exp_b: int) -> dict[str, Any]`.
- `meta` keys: `git_commit, stage, prompt_version, model, corpus, n_cases, reps, mean_score, mean_tokens_in, created_at, notes`. Each `scores` row: `case_id, rep, score, tokens_in, rationale, output_json, error`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_store.py
from benchmark.agent import store


def _meta(**over):
    base = dict(
        git_commit="abc123", stage="router", prompt_version="router/v1",
        model="gemini/gemini-2.5-flash", corpus="router.seed.jsonl",
        n_cases=2, reps=1, mean_score=0.5, mean_tokens_in=120.0,
        created_at="2026-06-29T10:00:00", notes="",
    )
    base.update(over)
    return base


def test_record_and_list_experiment(tmp_path):
    conn = store.connect(tmp_path / "eval.db")
    exp_id = store.record_experiment(
        conn,
        meta=_meta(),
        scores=[
            {"case_id": "r1", "rep": 0, "score": 1.0, "tokens_in": 100,
             "rationale": "intent match", "output_json": '{"intent":"paper_qa"}', "error": None},
            {"case_id": "r2", "rep": 0, "score": 0.0, "tokens_in": 140,
             "rationale": "intent miss", "output_json": '{"intent":"slides"}', "error": None},
        ],
    )
    assert isinstance(exp_id, int) and exp_id > 0
    rows = store.list_experiments(conn, stage="router")
    assert len(rows) == 1
    assert rows[0]["prompt_version"] == "router/v1"
    assert rows[0]["mean_score"] == 0.5
    scores = store.get_scores(conn, exp_id)
    assert {s["case_id"] for s in scores} == {"r1", "r2"}


def test_compare_two_experiments(tmp_path):
    conn = store.connect(tmp_path / "eval.db")
    a = store.record_experiment(conn, meta=_meta(prompt_version="router/v1", mean_score=0.5),
        scores=[{"case_id": "r1", "rep": 0, "score": 0.0, "tokens_in": 100, "rationale": "", "output_json": "{}", "error": None}])
    b = store.record_experiment(conn, meta=_meta(prompt_version="router/v2", mean_score=1.0),
        scores=[{"case_id": "r1", "rep": 0, "score": 1.0, "tokens_in": 80, "rationale": "", "output_json": "{}", "error": None}])
    cmp = store.compare(conn, a, b)
    assert cmp["mean_delta"] == 1.0
    per = {p["case_id"]: p for p in cmp["per_case"]}
    assert per["r1"]["a_score"] == 0.0 and per["r1"]["b_score"] == 1.0 and per["r1"]["delta"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/store.py
"""Local experiment store for per-stage prompt evaluation (SRS §III-9).

A gitignored SQLite file (default ``backend/benchmark/eval.db``) holding one
``eval_experiments`` row per (stage, prompt_version, model, commit) run plus a
``eval_scores`` row per case×rep — so "router/v2 raised mean 0.5 → 1.0 while
dropping prompt tokens 120 → 90" is a query, not a memory. Sync ``sqlite3``:
the harness is a sync CLI (like ``benchmark/runner.py``).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    stage TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    corpus TEXT NOT NULL,
    n_cases INTEGER NOT NULL,
    reps INTEGER NOT NULL,
    mean_score REAL,
    mean_tokens_in REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS eval_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL REFERENCES eval_experiments(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    rep INTEGER NOT NULL,
    score REAL,
    tokens_in INTEGER,
    rationale TEXT,
    output_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_eval_scores_exp ON eval_scores(experiment_id);
"""

_META_COLS = (
    "created_at", "git_commit", "stage", "prompt_version", "model",
    "corpus", "n_cases", "reps", "mean_score", "mean_tokens_in", "notes",
)


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def record_experiment(
    conn: sqlite3.Connection, *, meta: dict[str, Any], scores: list[dict[str, Any]],
) -> int:
    cols = ", ".join(_META_COLS)
    placeholders = ", ".join(f":{c}" for c in _META_COLS)
    row = {c: meta.get(c) for c in _META_COLS}
    cur = conn.execute(
        f"INSERT INTO eval_experiments ({cols}) VALUES ({placeholders})", row,
    )
    exp_id = int(cur.lastrowid or 0)
    conn.executemany(
        "INSERT INTO eval_scores "
        "(experiment_id, case_id, rep, score, tokens_in, rationale, output_json, error) "
        "VALUES (:experiment_id, :case_id, :rep, :score, :tokens_in, :rationale, :output_json, :error)",
        [{"experiment_id": exp_id, **s} for s in scores],
    )
    conn.commit()
    return exp_id


def list_experiments(conn: sqlite3.Connection, stage: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM eval_experiments"
    params: tuple[Any, ...] = ()
    if stage:
        sql += " WHERE stage = ?"
        params = (stage,)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_scores(conn: sqlite3.Connection, experiment_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM eval_scores WHERE experiment_id = ? ORDER BY id", (experiment_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _case_means(scores: list[dict[str, Any]]) -> dict[str, float]:
    by_case: dict[str, list[float]] = {}
    for s in scores:
        if s["score"] is not None:
            by_case.setdefault(s["case_id"], []).append(float(s["score"]))
    return {cid: sum(v) / len(v) for cid, v in by_case.items() if v}


def compare(conn: sqlite3.Connection, exp_a: int, exp_b: int) -> dict[str, Any]:
    a_means = _case_means(get_scores(conn, exp_a))
    b_means = _case_means(get_scores(conn, exp_b))
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

- [ ] **Step 5: Update `.gitignore`**

Append to `backend/benchmark/.gitignore`:

```
eval.db
agent/corpus/*.harvest.jsonl
```

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_store.py
git add benchmark/agent/__init__.py benchmark/agent/store.py \
        tests/benchmark_agent/__init__.py tests/benchmark_agent/test_store.py \
        benchmark/.gitignore
git commit -m "feat(eval): add eval.db experiment store for per-stage prompt eval"
```

---

## Task 2: Stage registry (router stage spec)

**Files:**
- Create: `backend/benchmark/agent/stages.py`
- Test: `backend/tests/benchmark_agent/test_stages.py`

**Interfaces:**
- Consumes: `paperhub.models.domain.RoutingDecision`, `Intent`.
- Produces: `StageSpec` (frozen dataclass, fields per "Shared types"); `STAGE_REGISTRY: dict[str, StageSpec]`; `get_stage(key: str) -> StageSpec`. Router entry: `key="router"`, `slot_base="router"`, `trace_agent="router"`, `trace_tool="classify"`, `response_model=RoutingDecision`. `variables_from_args` maps `{user_message, enabled_refs_count, slide_attached}`; `output_summary(RoutingDecision)` → `{intent, resolved_query, response_language, confidence}`; `deterministic_score(expect, output)` → `1.0` if `output["intent"] == expect["intent"]`, `0.0` on mismatch, `None` if `expect` has no `intent`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_stages.py
from paperhub.models.domain import RoutingDecision

from benchmark.agent.stages import STAGE_REGISTRY, get_stage


def test_router_spec_registered():
    spec = get_stage("router")
    assert spec.slot_base == "router"
    assert spec.trace_agent == "router" and spec.trace_tool == "classify"
    assert spec.response_model is RoutingDecision
    assert set(STAGE_REGISTRY) >= {"router"}


def test_router_variables_from_args_is_identity_on_the_three_vars():
    spec = get_stage("router")
    args = {"user_message": "compare these two", "enabled_refs_count": 2, "slide_attached": False, "$extra": "ignored"}
    assert spec.variables_from_args(args) == {
        "user_message": "compare these two", "enabled_refs_count": 2, "slide_attached": False,
    }


def test_router_output_summary_and_deterministic_score():
    spec = get_stage("router")
    decision = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                               reasoning="x", resolved_query="q", response_language="English")
    out = spec.output_summary(decision)
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

A ``StageSpec`` is the data a stage needs to be replayed + scored in isolation:
its prompt slot base, how it appears in the ``tool_calls`` trace, its structured
output model, and three small callables — map a recorded args dict to template
variables, summarise the stage output, and (optionally) score it deterministically.
Plan G1 ships the **router** stage; downstream stages are added in G2.
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
    slot_base: str
    trace_agent: str
    trace_tool: str
    response_model: type[BaseModel] | None
    variables_from_args: Callable[[dict[str, Any]], dict[str, Any]]
    output_summary: Callable[[Any], dict[str, Any]]
    deterministic_score: Callable[[dict[str, Any], dict[str, Any]], float | None]


def _router_variables(args: dict[str, Any]) -> dict[str, Any]:
    # The router records exactly its template variables (see router_node); pick
    # them explicitly so a redaction-added key never leaks into the render.
    return {
        "user_message": args["user_message"],
        "enabled_refs_count": args.get("enabled_refs_count", 0),
        "slide_attached": args.get("slide_attached", False),
    }


def _router_output(obj: Any) -> dict[str, Any]:
    d = obj if isinstance(obj, RoutingDecision) else RoutingDecision.model_validate(obj)
    return {
        "intent": d.intent,
        "resolved_query": d.resolved_query,
        "response_language": d.response_language,
        "confidence": d.confidence,
    }


def _router_score(expect: dict[str, Any], output: dict[str, Any]) -> float | None:
    want = expect.get("intent")
    if want is None:
        return None
    return 1.0 if output.get("intent") == want else 0.0


ROUTER = StageSpec(
    key="router",
    slot_base="router",
    trace_agent="router",
    trace_tool="classify",
    response_model=RoutingDecision,
    variables_from_args=_router_variables,
    output_summary=_router_output,
    deterministic_score=_router_score,
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
- Consumes: `StageSpec`/`get_stage` (Task 2); `tool_calls` columns `(run_id, agent, tool, args_redacted_json, result_summary_json, status)`.
- Produces: `CorpusCase` (dataclass per "Shared types"); `harvest(db_path: str | Path, stage: str, *, run_ids: list[int] | None = None, limit: int = 200) -> list[CorpusCase]`; `save_corpus(path, cases) -> None` (JSONL); `load_corpus(path) -> list[CorpusCase]`.
- `harvest` reads rows where `agent = spec.trace_agent AND tool = spec.trace_tool`, maps `args` via `spec.variables_from_args`, sets `observed = json.loads(result_summary_json)` and `expect = {"intent": observed["intent"]}` for the router (a *starting* label — a human corrects it for promoted failures). `case_id = f"run{run_id}-s{step_index}"`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_corpus.py
import json
import sqlite3

from benchmark.agent import corpus
from benchmark.agent.corpus import CorpusCase

_TOOL_CALLS_DDL = """
CREATE TABLE runs (id INTEGER PRIMARY KEY);
CREATE TABLE tool_calls (
    run_id INTEGER, branch TEXT, step_index INTEGER, parent_step INTEGER,
    agent TEXT, tool TEXT, model TEXT,
    args_redacted_json TEXT, result_summary_json TEXT,
    latency_ms INTEGER, token_in INTEGER, token_out INTEGER, status TEXT, error TEXT
);
"""


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(_TOOL_CALLS_DDL)
    conn.execute("INSERT INTO runs (id) VALUES (7)")
    conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, model, "
        "args_redacted_json, result_summary_json, status) VALUES (?,?,?,?,?,?,?,?)",
        (7, 0, "router", "classify", "gemini/gemini-2.5-flash",
         json.dumps({"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}),
         json.dumps({"intent": "paper_qa", "resolved_query": "what is MHA?", "response_language": "English", "confidence": 0.9}),
         "ok"),
    )
    # A non-router row that must be ignored.
    conn.execute(
        "INSERT INTO tool_calls (run_id, step_index, agent, tool, status) VALUES (7,1,'research','paper_qa:synthesize','ok')",
    )
    conn.commit()
    conn.close()


def test_harvest_router_cases(tmp_path):
    db = tmp_path / "paperhub.db"
    _seed(db)
    cases = corpus.harvest(db, "router")
    assert len(cases) == 1
    c = cases[0]
    assert c.stage == "router"
    assert c.variables == {"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}
    assert c.expect == {"intent": "paper_qa"}
    assert c.observed and c.observed["intent"] == "paper_qa"
    assert c.source_run_id == 7


def test_save_and_load_roundtrip(tmp_path):
    cases = [CorpusCase(case_id="x1", stage="router",
                        variables={"user_message": "hi", "enabled_refs_count": 0, "slide_attached": False},
                        expect={"intent": "chitchat"}, rubric="greeting → chitchat")]
    p = tmp_path / "router.seed.jsonl"
    corpus.save_corpus(p, cases)
    back = corpus.load_corpus(p)
    assert back == cases
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.corpus'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/corpus.py
"""Per-stage eval corpus — real inputs harvested from the trace, JSONL on disk.

A ``CorpusCase`` is a stage's recorded input state (the template variables) plus
reference labels. Inputs come from ACTUAL runs (``tool_calls``) — never synthesized
— so a stage is measured on what it really sees, and a failed run can be promoted
into the corpus verbatim (SRS §III-9). ``expect`` is seeded from the recorded
output as a starting label; for a promoted *failure* a human corrects it.
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
            "FROM tool_calls WHERE agent = ? AND tool = ? "
            "AND args_redacted_json IS NOT NULL"
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
            args = json.loads(r["args_redacted_json"])
            variables = spec.variables_from_args(args)
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
            case_id=f"run{r['run_id']}-s{r['step_index']}",
            stage=stage, variables=variables, expect=expect,
            source_run_id=int(r["run_id"]), observed=observed,
        ))
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

## Task 4: Adapter `build_messages` + per-stage replay

**Files:**
- Modify: `backend/src/paperhub/llm/litellm_adapter.py` (add public `build_messages`)
- Create: `backend/benchmark/agent/replay.py`
- Test: `backend/tests/benchmark_agent/test_replay.py`

**Interfaces:**
- Consumes: `StageSpec` (Task 2), `CorpusCase` (Task 3), an adapter exposing `structured(...)` and `build_messages(...)`.
- Produces (src): `LiteLlmAdapter.build_messages(self, slot: str, variables: dict[str, Any], history: list[dict[str, str]] | None = None) -> list[dict[str, str]]` — thin public alias of `_messages`.
- Produces (eval): `ReplayOutput` (dataclass); `async def replay_stage(spec: StageSpec, version: str, case: CorpusCase, *, adapter: Any, model: str, count_tokens: Callable[[str, list[dict[str, str]]], int | None] | None = None) -> ReplayOutput`. Builds the slot `f"{spec.slot_base}/{version}"`, renders messages (for token count), calls `adapter.structured(slot=…, variables=case.variables, response_model=spec.response_model, model=model)`, returns `spec.output_summary(...)`. On exception, `ReplayOutput(output={}, tokens_in=…, error=str(exc))`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_replay.py
import pytest

from paperhub.models.domain import RoutingDecision

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.replay import replay_stage
from benchmark.agent.stages import get_stage


class _StubAdapter:
    """Records the slot/variables it was called with; returns a canned decision."""
    def __init__(self, decision: RoutingDecision):
        self._decision = decision
        self.calls: list[dict] = []

    def build_messages(self, slot, variables, history=None):
        return [{"role": "system", "content": f"slot={slot}"},
                {"role": "user", "content": variables["user_message"]}]

    async def structured(self, *, slot, variables, response_model, model, history=None, **kw):
        self.calls.append({"slot": slot, "variables": variables, "model": model})
        return self._decision


def _case():
    return CorpusCase(case_id="c1", stage="router",
                      variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False},
                      expect={"intent": "paper_qa"})


@pytest.mark.asyncio
async def test_replay_uses_slot_version_and_recorded_variables():
    spec = get_stage("router")
    decision = RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.8,
                               reasoning="x", resolved_query="what is MHA?", response_language="English")
    adapter = _StubAdapter(decision)
    out = await replay_stage(spec, "v2", _case(), adapter=adapter, model="gemini/gemini-2.5-flash",
                             count_tokens=lambda model, msgs: 42)
    assert out.error is None
    assert out.output["intent"] == "paper_qa"
    assert out.tokens_in == 42
    # The replay drove the v2 slot over the SAME recorded variables.
    assert adapter.calls[0]["slot"] == "router/v2"
    assert adapter.calls[0]["variables"]["user_message"] == "what is MHA?"


@pytest.mark.asyncio
async def test_replay_captures_error_without_raising():
    spec = get_stage("router")

    class _Boom(_StubAdapter):
        async def structured(self, **kw):
            raise RuntimeError("provider 500")

    out = await replay_stage(spec, "v1", _case(),
                             adapter=_Boom(RoutingDecision(intent="chitchat", model_tier="small",
                                                           confidence=0.1, reasoning="x")),
                             model="m", count_tokens=lambda model, msgs: 10)
    assert out.error is not None and "provider 500" in out.error
    assert out.output == {} and out.tokens_in == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.replay'`.

- [ ] **Step 3a: Add `build_messages` to the adapter**

In `backend/src/paperhub/llm/litellm_adapter.py`, immediately after the `_messages` method (around line 157), add:

```python
    def build_messages(
        self,
        slot: str,
        variables: dict[str, Any],
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """Public alias of ``_messages`` — the rendered (system, history, user)
        message list for a slot+variables. Used by the per-stage eval harness
        (benchmark/agent) to count prompt tokens against the EXACT messages the
        adapter would send, without re-implementing prompt rendering."""
        return self._messages(slot, variables, history)
```

- [ ] **Step 3b: Write the replay module**

```python
# backend/benchmark/agent/replay.py
"""Replay one agent stage with a chosen prompt version (SRS §III-9).

The keystone of per-stage eval: the production ``LiteLlmAdapter`` already renders
a prompt from ``slot`` + ``variables``, so replaying a stage with a DIFFERENT
prompt version is just a different ``slot`` over the SAME recorded input state.
The prompt is the only variable. In-process — no live backend.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.stages import StageSpec


@dataclass
class ReplayOutput:
    output: dict[str, Any]
    tokens_in: int | None
    error: str | None = None
    raw: Any = field(default=None)


def _litellm_token_counter(model: str, messages: list[dict[str, str]]) -> int | None:
    try:
        import litellm
        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:  # noqa: BLE001 — token counting is best-effort
        return None


async def replay_stage(
    spec: StageSpec,
    version: str,
    case: CorpusCase,
    *,
    adapter: Any,
    model: str,
    count_tokens: Callable[[str, list[dict[str, str]]], int | None] | None = None,
) -> ReplayOutput:
    slot = f"{spec.slot_base}/{version}"
    counter = count_tokens or _litellm_token_counter
    try:
        messages = adapter.build_messages(slot, case.variables)
        tokens_in = counter(model, messages)
    except Exception:  # noqa: BLE001 — a bad template var shouldn't crash the sweep
        tokens_in = None
    try:
        result = await adapter.structured(
            slot=slot, variables=case.variables,
            response_model=spec.response_model, model=model,
        )
        return ReplayOutput(output=spec.output_summary(result), tokens_in=tokens_in, raw=result)
    except Exception as exc:  # noqa: BLE001 — capture, don't abort the corpus
        return ReplayOutput(output={}, tokens_in=tokens_in, error=str(exc))
```

- [ ] **Step 4: Run tests + the src gate**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_replay.py -v; uv run mypy src/paperhub/llm/litellm_adapter.py; uv run ruff check src/paperhub/llm/litellm_adapter.py tests/benchmark_agent/test_replay.py`
Expected: tests PASS (2 passed); mypy + ruff clean.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/paperhub/llm/litellm_adapter.py benchmark/agent/replay.py tests/benchmark_agent/test_replay.py
git commit -m "feat(eval): per-stage replay reusing the production adapter

Add LiteLlmAdapter.build_messages (public alias of _messages) so the eval
harness counts prompt tokens against the exact rendered messages."
```

---

## Task 5: Grader — deterministic + scalar/pairwise judge

**Files:**
- Create: `backend/benchmark/agent/grade.py`
- Test: `backend/tests/benchmark_agent/test_grade.py`

**Interfaces:**
- Consumes: `StageSpec` (Task 2), `CorpusCase` (Task 3), `ReplayOutput` (Task 4). Reuses `benchmark.judge.JudgeVerdict` discipline (temp 0) but adds stage-output judges here (judge.py stays the end-to-end gate).
- Produces: `CaseScore` (dataclass per "Shared types"); `async def score_case(spec, case, replay, rep, *, judge_model: str | None = None, judge_fn=None) -> CaseScore` — deterministic score first; if it returns `None` and `judge_model` is set, call `judge_fn` (defaults to `judge_scalar`), normalized to 0..1. `async def judge_scalar(*, request: str, rubric: str, output_text: str, model: str) -> tuple[float, str]` (1-10 ÷ 10 → 0..1, plus rationale). `async def judge_pairwise(*, request: str, rubric: str, output_a: str, output_b: str, model: str) -> str` (`'A'|'B'|'tie'`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_grade.py
import pytest

from benchmark.agent import grade
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.replay import ReplayOutput
from benchmark.agent.stages import get_stage


def _case(expect):
    return CorpusCase(case_id="c1", stage="router",
                      variables={"user_message": "q", "enabled_refs_count": 0, "slide_attached": False},
                      expect=expect, rubric="route to the right intent")


@pytest.mark.asyncio
async def test_deterministic_score_does_not_call_judge():
    spec = get_stage("router")
    replay = ReplayOutput(output={"intent": "paper_qa"}, tokens_in=88)

    async def _boom(**kw):  # judge must NOT be called when deterministic fires
        raise AssertionError("judge should not run for the router (deterministic)")

    s = await grade.score_case(spec, _case({"intent": "paper_qa"}), replay, 0,
                               judge_model="x", judge_fn=_boom)
    assert s.score == 1.0 and s.tokens_in == 88 and s.error is None
    miss = await grade.score_case(spec, _case({"intent": "slides"}), replay, 0,
                                  judge_model="x", judge_fn=_boom)
    assert miss.score == 0.0


@pytest.mark.asyncio
async def test_errored_replay_scores_zero():
    spec = get_stage("router")
    replay = ReplayOutput(output={}, tokens_in=12, error="provider 500")
    s = await grade.score_case(spec, _case({"intent": "paper_qa"}), replay, 1)
    assert s.score == 0.0 and s.error == "provider 500"


@pytest.mark.asyncio
async def test_judge_scalar_parses_and_normalizes(monkeypatch):
    async def _fake_acompletion(**kw):
        return {"choices": [{"message": {"content": '{"score": 8, "rationale": "good"}'}}]}

    monkeypatch.setattr(grade.litellm, "acompletion", _fake_acompletion)
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

Two scores feed an experiment: OUTPUT quality (here) and PROMPT quality (token
count, carried on ReplayOutput). Output quality is deterministic where possible
(the router's intent is an exact-match check) and an LLM judge otherwise. Judges
are temp-0 for reproducibility — same discipline as benchmark/judge.py — and
normalised to 0..1 so deterministic 0/1 and scalar 1-10 aggregate coherently.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import litellm
from pydantic import BaseModel, Field

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.replay import ReplayOutput
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
    score: int = Field(ge=1, le=10, description="1 (poor) to 10 (perfect)")
    rationale: str = Field(description="one or two sentences")


class _PairwiseVerdict(BaseModel):
    winner: str = Field(description="'A', 'B', or 'tie'")
    rationale: str = Field(description="one or two sentences")


JudgeFn = Callable[..., Awaitable[tuple[float, str]]]


async def judge_scalar(*, request: str, rubric: str, output_text: str, model: str) -> tuple[float, str]:
    system = (
        "You are a strict, reproducible evaluator of one agent stage's output. "
        "Score 1 (poor) to 10 (perfect) on whether the output correctly and "
        "concisely satisfies the request per the rubric. Return the structured verdict."
    )
    user = (
        f"## Request\n{request}\n\n## Rubric\n{rubric or '(general correctness)'}\n\n"
        f"## Stage output\n{output_text}\n\nScore 1-10."
    )
    resp = await litellm.acompletion(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=_ScalarVerdict, temperature=JUDGE_TEMPERATURE,
    )
    v = _ScalarVerdict.model_validate_json(resp["choices"][0]["message"]["content"])
    return v.score / 10.0, v.rationale


async def judge_pairwise(*, request: str, rubric: str, output_a: str, output_b: str, model: str) -> str:
    system = (
        "You compare two agent-stage outputs (A and B) for the same request. "
        "Pick the better one per the rubric, or 'tie' if indistinguishable. "
        "Pairwise judgements are more reliable than absolute scores — be decisive."
    )
    user = (
        f"## Request\n{request}\n\n## Rubric\n{rubric or '(general correctness)'}\n\n"
        f"## Output A\n{output_a}\n\n## Output B\n{output_b}\n\nWhich is better: A, B, or tie?"
    )
    resp = await litellm.acompletion(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=_PairwiseVerdict, temperature=JUDGE_TEMPERATURE,
    )
    v = _PairwiseVerdict.model_validate_json(resp["choices"][0]["message"]["content"])
    w = v.winner.strip().upper()
    return "A" if w == "A" else "B" if w == "B" else "tie"


async def score_case(
    spec: StageSpec,
    case: CorpusCase,
    replay: ReplayOutput,
    rep: int,
    *,
    judge_model: str | None = None,
    judge_fn: JudgeFn | None = None,
) -> CaseScore:
    if replay.error:
        return CaseScore(case_id=case.case_id, rep=rep, score=0.0, tokens_in=replay.tokens_in,
                         rationale=f"replay errored: {replay.error[:160]}", output=replay.output,
                         error=replay.error)
    det = spec.deterministic_score(case.expect, replay.output)
    if det is not None:
        return CaseScore(case_id=case.case_id, rep=rep, score=det, tokens_in=replay.tokens_in,
                         rationale="deterministic check", output=replay.output)
    if judge_model is None:
        return CaseScore(case_id=case.case_id, rep=rep, score=None, tokens_in=replay.tokens_in,
                         rationale="no deterministic check and no judge configured", output=replay.output)
    fn = judge_fn or judge_scalar
    score, rationale = await fn(
        request=str(case.variables.get("user_message", "")),
        rubric=case.rubric, output_text=str(replay.output), model=judge_model,
    )
    return CaseScore(case_id=case.case_id, rep=rep, score=score, tokens_in=replay.tokens_in,
                     rationale=rationale, output=replay.output)
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

## Task 6: Experiment runner (corpus × reps → aggregate)

**Files:**
- Create: `backend/benchmark/agent/experiment.py`
- Test: `backend/tests/benchmark_agent/test_experiment.py`

**Interfaces:**
- Consumes: `StageSpec`, `CorpusCase`, `replay_stage` (Task 4), `score_case` (Task 5).
- Produces: `ExperimentMeta`, `ExperimentResult` (dataclasses per "Shared types"); `async def run_experiment(spec, version, corpus, *, adapter, model, reps=1, judge_model=None, count_tokens=None, git_commit="unknown", created_at="", notes="") -> ExperimentResult`. Runs each case `reps` times (variance), aggregates `mean_score` (over non-None scores) and `mean_tokens_in`. `to_store_payload(result) -> tuple[dict, list[dict]]` shapes it for `store.record_experiment`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_experiment.py
import pytest

from paperhub.models.domain import RoutingDecision

from benchmark.agent import store
from benchmark.agent.corpus import CorpusCase
from benchmark.agent.experiment import run_experiment, to_store_payload
from benchmark.agent.stages import get_stage


class _StubAdapter:
    def __init__(self, intent: str):
        self._intent = intent

    def build_messages(self, slot, variables, history=None):
        return [{"role": "user", "content": variables["user_message"]}]

    async def structured(self, *, slot, variables, response_model, model, history=None, **kw):
        return RoutingDecision(intent=self._intent, model_tier="small", confidence=0.9,
                               reasoning="x", resolved_query=variables["user_message"], response_language="English")


def _corpus():
    return [
        CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"},
                   variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False}),
        CorpusCase(case_id="c2", stage="router", expect={"intent": "slides"},
                   variables={"user_message": "make slides", "enabled_refs_count": 1, "slide_attached": False}),
    ]


@pytest.mark.asyncio
async def test_run_experiment_aggregates_and_persists(tmp_path):
    spec = get_stage("router")
    # Stub always predicts paper_qa: c1 scores 1.0, c2 scores 0.0 → mean 0.5.
    result = await run_experiment(spec, "v1", _corpus(), adapter=_StubAdapter("paper_qa"),
                                  model="gemini/gemini-2.5-flash", reps=2,
                                  count_tokens=lambda model, msgs: 100,
                                  git_commit="abc", created_at="2026-06-29T10:00:00")
    assert result.mean_score == 0.5
    assert result.mean_tokens_in == 100.0
    assert len(result.scores) == 4  # 2 cases × 2 reps

    conn = store.connect(tmp_path / "eval.db")
    meta, rows = to_store_payload(result)
    exp_id = store.record_experiment(conn, meta=meta, scores=rows)
    assert store.list_experiments(conn)[0]["mean_score"] == 0.5
    assert len(store.get_scores(conn, exp_id)) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_experiment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.experiment'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/experiment.py
"""Run one experiment: a prompt version over a corpus, N reps, aggregated.

An experiment is the unit you compare across prompt versions/commits. Reps give
variance so a score delta is signal, not judge noise (SRS §III-9). The result is
shaped by ``to_store_payload`` for the eval.db store (Task 1).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.grade import CaseScore, JudgeFn, score_case
from benchmark.agent.replay import replay_stage
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
    spec: StageSpec,
    version: str,
    corpus: list[CorpusCase],
    *,
    adapter: Any,
    model: str,
    reps: int = 1,
    judge_model: str | None = None,
    judge_fn: JudgeFn | None = None,
    count_tokens: Callable[[str, list[dict[str, str]]], int | None] | None = None,
    git_commit: str = "unknown",
    created_at: str = "",
    corpus_name: str = "",
    notes: str = "",
) -> ExperimentResult:
    scores: list[CaseScore] = []
    for case in corpus:
        for rep in range(reps):
            replay = await replay_stage(spec, version, case, adapter=adapter,
                                        model=model, count_tokens=count_tokens)
            scores.append(await score_case(spec, case, replay, rep,
                                           judge_model=judge_model, judge_fn=judge_fn))
    mean_score = _mean([s.score for s in scores if s.score is not None])
    mean_tokens = _mean([float(s.tokens_in) for s in scores if s.tokens_in is not None])
    meta = ExperimentMeta(
        git_commit=git_commit, stage=spec.key, prompt_version=f"{spec.slot_base}/{version}",
        model=model, corpus=corpus_name, reps=reps, created_at=created_at, notes=notes,
    )
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_experiment.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_experiment.py
git add benchmark/agent/experiment.py tests/benchmark_agent/test_experiment.py
git commit -m "feat(eval): experiment runner — corpus x reps, aggregate, persist"
```

---

## Task 7: Golden-output emission (freeze + propagate primitive)

**Files:**
- Modify: `backend/benchmark/agent/corpus.py` (add `emit_golden`)
- Test: `backend/tests/benchmark_agent/test_golden.py`

**Interfaces:**
- Consumes: `StageSpec`, `CorpusCase`, `replay_stage` (Task 4).
- Produces: `async def emit_golden(spec, version, corpus, *, adapter, model, count_tokens=None) -> list[dict[str, Any]]` — runs the FROZEN winning version over the corpus and returns `[{case_id, source_run_id, variables, output}]`. This is the cascade hinge (SRS §III-9 step 5): a frozen stage's golden outputs become the next stage's real input set. (Wiring those outputs into a downstream stage's `variables` is G2, when stage 2 exists.)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_golden.py
import pytest

from paperhub.models.domain import RoutingDecision

from benchmark.agent.corpus import CorpusCase, emit_golden
from benchmark.agent.stages import get_stage


class _StubAdapter:
    def build_messages(self, slot, variables, history=None):
        return [{"role": "user", "content": variables["user_message"]}]

    async def structured(self, *, slot, variables, response_model, model, history=None, **kw):
        return RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                               reasoning="x", resolved_query=variables["user_message"], response_language="English")


@pytest.mark.asyncio
async def test_emit_golden_runs_frozen_version_over_corpus():
    spec = get_stage("router")
    corpus = [CorpusCase(case_id="c1", stage="router", expect={"intent": "paper_qa"},
                         source_run_id=7,
                         variables={"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False})]
    golden = await emit_golden(spec, "v2", corpus, adapter=_StubAdapter(), model="m",
                               count_tokens=lambda model, msgs: 0)
    assert golden == [{
        "case_id": "c1", "source_run_id": 7,
        "variables": {"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": False},
        "output": {"intent": "paper_qa", "resolved_query": "what is MHA?",
                   "response_language": "English", "confidence": 0.9},
    }]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_golden.py -v`
Expected: FAIL — `ImportError: cannot import name 'emit_golden'`.

- [ ] **Step 3: Add `emit_golden` to `corpus.py`**

Append to `backend/benchmark/agent/corpus.py` (add the imports at the top of the file if not present: `from collections.abc import Callable`, and the local imports inside the function to avoid a circular import with `replay`):

```python
async def emit_golden(
    spec: "StageSpec",
    version: str,
    corpus: list[CorpusCase],
    *,
    adapter: Any,
    model: str,
    count_tokens: "Callable[[str, list[dict[str, Any]]], int | None] | None" = None,
) -> list[dict[str, Any]]:
    """Run the FROZEN winning prompt version over the corpus and return its
    golden outputs — the cascade hinge (SRS §III-9 step 5): these become the
    next stage's real input set, never synthesized."""
    from benchmark.agent.replay import replay_stage  # local: avoid import cycle

    out: list[dict[str, Any]] = []
    for case in corpus:
        r = await replay_stage(spec, version, case, adapter=adapter, model=model, count_tokens=count_tokens)
        out.append({
            "case_id": case.case_id, "source_run_id": case.source_run_id,
            "variables": case.variables, "output": r.output,
        })
    return out
```

Add the `StageSpec` import for typing at the top of `corpus.py`:

```python
from benchmark.agent.stages import StageSpec, get_stage
```

(replace the existing `from benchmark.agent.stages import get_stage` line).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_golden.py tests/benchmark_agent/test_corpus.py -v`
Expected: PASS (3 passed total — golden + the two corpus tests still green).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check benchmark/agent/corpus.py tests/benchmark_agent/test_golden.py
git add benchmark/agent/corpus.py tests/benchmark_agent/test_golden.py
git commit -m "feat(eval): emit_golden — freeze a version + propagate golden outputs"
```

---

## Task 8: CLI (`harvest | run | compare | list | golden`) + launcher

**Files:**
- Create: `backend/benchmark/agent/cli.py`
- Create: `backend/scripts/run-eval.ps1`
- Test: `backend/tests/benchmark_agent/test_cli.py`

**Interfaces:**
- Consumes: every module above.
- Produces: `def main(argv: list[str] | None = None) -> int`. Subcommands:
  - `harvest --db <path> --stage router --out <corpus.jsonl> [--run-ids 1,2] [--limit N]`
  - `run --stage router --version v1 --corpus <jsonl> --model <m> [--reps N] [--judge-model m] [--store <eval.db>] [--env <.env>] [--notes "..."]`
  - `golden --stage router --version v2 --corpus <jsonl> --model <m> --out <golden.jsonl> [--env <.env>]`
  - `compare --store <eval.db> --a <id> --b <id>`
  - `list --store <eval.db> [--stage router]`
- `run`/`golden` build a real `LiteLlmAdapter`; `run` resolves git commit + timestamp, persists via `to_store_payload` + `store.record_experiment`, prints the experiment id + mean. Tests inject a fake adapter via the module-level seam `_make_adapter` (monkeypatched) so no network is touched.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_cli.py
import pytest

from paperhub.models.domain import RoutingDecision

from benchmark.agent import cli, store


class _StubAdapter:
    def build_messages(self, slot, variables, history=None):
        return [{"role": "user", "content": variables["user_message"]}]

    async def structured(self, *, slot, variables, response_model, model, history=None, **kw):
        return RoutingDecision(intent="paper_qa", model_tier="small", confidence=0.9,
                               reasoning="x", resolved_query=variables["user_message"], response_language="English")


def _write_corpus(path):
    path.write_text(
        '{"case_id": "c1", "stage": "router", '
        '"variables": {"user_message": "what is MHA?", "enabled_refs_count": 1, "slide_attached": false}, '
        '"expect": {"intent": "paper_qa"}, "rubric": "", "source_run_id": 7, "observed": null}\n'
        '{"case_id": "c2", "stage": "router", '
        '"variables": {"user_message": "make slides", "enabled_refs_count": 1, "slide_attached": false}, '
        '"expect": {"intent": "slides"}, "rubric": "", "source_run_id": 8, "observed": null}\n',
        encoding="utf-8",
    )


def test_run_then_list_and_compare(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_make_adapter", lambda: _StubAdapter())
    monkeypatch.setattr(cli, "_token_counter", lambda model, msgs: 100)
    corpus = tmp_path / "router.seed.jsonl"
    _write_corpus(corpus)
    db = tmp_path / "eval.db"

    rc = cli.main(["run", "--stage", "router", "--version", "v1", "--corpus", str(corpus),
                   "--model", "gemini/gemini-2.5-flash", "--reps", "1", "--store", str(db)])
    assert rc == 0
    conn = store.connect(db)
    exps = store.list_experiments(conn, stage="router")
    assert len(exps) == 1 and exps[0]["mean_score"] == 0.5  # c1 hit, c2 miss

    rc = cli.main(["list", "--store", str(db), "--stage", "router"])
    assert rc == 0
    assert "router/v1" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.cli'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/benchmark/agent/cli.py
"""CLI for per-stage prompt evaluation (SRS §III-9).

    uv run python -m benchmark.agent.cli harvest --db workspace/paperhub.db --stage router --out benchmark/agent/corpus/router.harvest.jsonl
    uv run python -m benchmark.agent.cli run --stage router --version v1 --corpus benchmark/agent/corpus/router.seed.jsonl --model gemini/gemini-2.5-flash --store benchmark/eval.db
    uv run python -m benchmark.agent.cli compare --store benchmark/eval.db --a 1 --b 2
    uv run python -m benchmark.agent.cli list --store benchmark/eval.db --stage router
    uv run python -m benchmark.agent.cli golden --stage router --version v2 --corpus <seed> --model <m> --out <golden.jsonl>
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
from benchmark.agent.experiment import run_experiment, to_store_payload
from benchmark.agent.stages import STAGE_REGISTRY, get_stage


def _make_adapter() -> Any:
    """Seam: real adapter in production, monkeypatched in tests."""
    from paperhub.llm.litellm_adapter import LiteLlmAdapter
    return LiteLlmAdapter()


def _token_counter(model: str, messages: list[dict[str, str]]) -> int | None:
    from benchmark.agent.replay import _litellm_token_counter
    return _litellm_token_counter(model, messages)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
        ).strip() or "unknown"
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
    corpus = corpus_mod.load_corpus(args.corpus)
    adapter = _make_adapter()
    result = asyncio.run(run_experiment(
        spec, args.version, corpus, adapter=adapter, model=args.model, reps=args.reps,
        judge_model=(args.judge_model or None), count_tokens=_token_counter,
        git_commit=_git_commit(), created_at=datetime.now().isoformat(timespec="seconds"),
        corpus_name=Path(args.corpus).name, notes=args.notes,
    ))
    conn = store.connect(args.store)
    meta, rows = to_store_payload(result)
    exp_id = store.record_experiment(conn, meta=meta, scores=rows)
    print(f"experiment {exp_id}: {result.meta.prompt_version} model={args.model} "
          f"mean_score={result.mean_score} mean_tokens_in={result.mean_tokens_in} "
          f"(n={meta['n_cases']}, reps={args.reps})")
    return 0


def _cmd_golden(args: argparse.Namespace) -> int:
    _load_env(args.env)
    spec = get_stage(args.stage)
    corpus = corpus_mod.load_corpus(args.corpus)
    adapter = _make_adapter()
    golden = asyncio.run(corpus_mod.emit_golden(
        spec, args.version, corpus, adapter=adapter, model=args.model, count_tokens=_token_counter,
    ))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("w", encoding="utf-8") as fh:
        for g in golden:
            fh.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"Wrote {len(golden)} golden output(s) -> {args.out}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    conn = store.connect(args.store)
    cmp = store.compare(conn, args.a, args.b)
    print(f"compare exp {args.a} -> {args.b}: mean {cmp['a_mean']} -> {cmp['b_mean']} "
          f"(delta {cmp['mean_delta']})")
    for p in cmp["per_case"]:
        print(f"  {p['case_id']}: {p['a_score']} -> {p['b_score']} (delta {p['delta']})")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    conn = store.connect(args.store)
    for e in store.list_experiments(conn, stage=(args.stage or None)):
        print(f"  [{e['id']}] {e['created_at']} {e['prompt_version']} model={e['model']} "
              f"commit={e['git_commit']} mean_score={e['mean_score']} "
              f"mean_tokens_in={e['mean_tokens_in']} (n={e['n_cases']}, reps={e['reps']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="benchmark.agent.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    stages = sorted(STAGE_REGISTRY)

    h = sub.add_parser("harvest", help="build a per-stage corpus from the trace DB")
    h.add_argument("--db", required=True)
    h.add_argument("--stage", required=True, choices=stages)
    h.add_argument("--out", required=True)
    h.add_argument("--run-ids", default="")
    h.add_argument("--limit", type=int, default=200)
    h.set_defaults(fn=_cmd_harvest)

    r = sub.add_parser("run", help="run a prompt version over a corpus + persist an experiment")
    r.add_argument("--stage", required=True, choices=stages)
    r.add_argument("--version", required=True)
    r.add_argument("--corpus", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--judge-model", default="")
    r.add_argument("--store", default="benchmark/eval.db")
    r.add_argument("--env", default=".env")
    r.add_argument("--notes", default="")
    r.set_defaults(fn=_cmd_run)

    g = sub.add_parser("golden", help="emit a frozen version's golden outputs")
    g.add_argument("--stage", required=True, choices=stages)
    g.add_argument("--version", required=True)
    g.add_argument("--corpus", required=True)
    g.add_argument("--model", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--env", default=".env")
    g.set_defaults(fn=_cmd_golden)

    c = sub.add_parser("compare", help="diff two experiments")
    c.add_argument("--store", default="benchmark/eval.db")
    c.add_argument("--a", type=int, required=True)
    c.add_argument("--b", type=int, required=True)
    c.set_defaults(fn=_cmd_compare)

    li = sub.add_parser("list", help="list experiments")
    li.add_argument("--store", default="benchmark/eval.db")
    li.add_argument("--stage", default="")
    li.set_defaults(fn=_cmd_list)

    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add the PowerShell launcher**

Create `backend/scripts/run-eval.ps1`:

```powershell
# Launcher for the per-stage prompt-eval CLI (SRS §III-9). Runs in-process —
# no live backend needed (it calls the LLM via the adapter directly). Needs an
# LLM API key in backend/.env. Examples:
#   scripts/run-eval.ps1 run --stage router --version v1 --corpus benchmark/agent/corpus/router.seed.jsonl --model gemini/gemini-2.5-flash
#   scripts/run-eval.ps1 list --stage router
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Args)
uv run python -m benchmark.agent.cli @Args
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

## Task 9: Config-driven sweep — variants × test-set buckets

**Files:**
- Create: `backend/benchmark/agent/eval_config.py`
- Create: `backend/benchmark/agent/sweep.py`
- Modify: `backend/benchmark/agent/cli.py` (add the `sweep` verb)
- Test: `backend/tests/benchmark_agent/test_sweep.py`

**Interfaces:**
- Consumes: `run_experiment`/`to_store_payload` (Task 6), `store` (Task 1), `get_stage` (Task 2), `load_corpus` (Task 3).
- Produces:
  - `eval_config.py`: `TestSet(name: str, corpus: str)`; `EvalConfig(stage, model, variants: list[str], testsets: list[TestSet], reps=1, judge_model: str | None = None, store="benchmark/eval.db")`; `load_eval_config(path) -> EvalConfig`.
  - `sweep.py`: `SweepCell(variant: str, testset: str, experiment_id: int, mean_score: float | None, mean_tokens_in: float | None)`; `async run_sweep(cfg, *, adapter, store_conn, git_commit, created_at, count_tokens=None) -> list[SweepCell]`; `matrix_report(cfg, cells) -> str`.
  - `cli.py`: `sweep --config <toml> [--out <md>] [--env <.env>]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/benchmark_agent/test_sweep.py
import pytest

from paperhub.models.domain import RoutingDecision

from benchmark.agent import store
from benchmark.agent.eval_config import load_eval_config
from benchmark.agent.sweep import matrix_report, run_sweep


class _VersionStub:
    """Returns paper_qa for v1, slides for v2 — so the two variants diverge."""
    def build_messages(self, slot, variables, history=None):
        return [{"role": "user", "content": variables["user_message"]}]

    async def structured(self, *, slot, variables, response_model, model, history=None, **kw):
        intent = "slides" if slot.endswith("/v2") else "paper_qa"
        return RoutingDecision(intent=intent, model_tier="small", confidence=0.9,
                               reasoning="x", resolved_query=variables["user_message"], response_language="English")


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_eval_config(tmp_path):
    core = tmp_path / "core.jsonl"
    _write(core, ['{"case_id":"a","stage":"router","variables":{"user_message":"q","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"}}'])
    toml = tmp_path / "router.eval.toml"
    toml.write_text(
        '[eval]\nstage="router"\nmodel="gemini/gemini-2.5-flash"\nreps=2\nvariants=["v1","v2"]\n'
        f'store="{(tmp_path / "eval.db").as_posix()}"\n\n'
        f'[[testsets]]\nname="core"\ncorpus="{core.as_posix()}"\n',
        encoding="utf-8",
    )
    cfg = load_eval_config(toml)
    assert cfg.stage == "router" and cfg.reps == 2
    assert cfg.variants == ["v1", "v2"]
    assert len(cfg.testsets) == 1 and cfg.testsets[0].name == "core"


@pytest.mark.asyncio
async def test_run_sweep_grid_and_matrix_report(tmp_path):
    core = tmp_path / "core.jsonl"
    _write(core, ['{"case_id":"a","stage":"router","variables":{"user_message":"q","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"paper_qa"}}'])
    reg = tmp_path / "regression.jsonl"
    _write(reg, ['{"case_id":"b","stage":"router","variables":{"user_message":"deck","enabled_refs_count":1,"slide_attached":false},"expect":{"intent":"slides"}}'])
    toml = tmp_path / "router.eval.toml"
    toml.write_text(
        '[eval]\nstage="router"\nmodel="m"\nreps=1\nvariants=["v1","v2"]\n'
        f'store="{(tmp_path / "eval.db").as_posix()}"\n\n'
        f'[[testsets]]\nname="core"\ncorpus="{core.as_posix()}"\n\n'
        f'[[testsets]]\nname="regression"\ncorpus="{reg.as_posix()}"\n',
        encoding="utf-8",
    )
    cfg = load_eval_config(toml)
    conn = store.connect(cfg.store)
    cells = await run_sweep(cfg, adapter=_VersionStub(), store_conn=conn,
                            git_commit="abc", created_at="2026-06-29T10:00:00",
                            count_tokens=lambda model, msgs: 100)
    assert len(cells) == 4  # 2 variants × 2 testsets
    assert len(store.list_experiments(conn)) == 4
    md = matrix_report(cfg, cells)
    assert "router/v1" in md and "router/v2" in md
    assert "core" in md and "regression" in md
    # v1 predicts paper_qa (core hit, regression miss); v2 predicts slides (core miss, regression hit).
    assert "-1.00" in md and "⚠" in md   # core regressed v1 -> v2
    assert "+1.00" in md                  # regression improved v1 -> v2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmark.agent.eval_config'`.

- [ ] **Step 3a: Write the config loader**

```python
# backend/benchmark/agent/eval_config.py
"""Per-agent eval config (SRS §III-9): which prompt VARIANTS to compare over
which TEST-SET buckets. Edited by a human or by Claude — the declarative front
end to a sweep. TOML, matching the existing ``benchmark/cases.*.toml`` idiom.
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
    store: str = "benchmark/eval.db"


def load_eval_config(path: str | Path) -> EvalConfig:
    with Path(path).open("rb") as fh:
        raw = tomllib.load(fh)
    ev = raw.get("eval", {})
    if "stage" not in ev or "model" not in ev:
        raise ValueError("eval config: [eval] must set 'stage' and 'model'")
    variants = [str(v) for v in ev.get("variants", [])]
    if not variants:
        raise ValueError("eval config: [eval].variants must be a non-empty list")
    testsets = [
        TestSet(name=str(t["name"]), corpus=str(t["corpus"]))
        for t in raw.get("testsets", [])
    ]
    if not testsets:
        raise ValueError("eval config: at least one [[testsets]] is required")
    return EvalConfig(
        stage=str(ev["stage"]), model=str(ev["model"]), variants=variants, testsets=testsets,
        reps=int(ev.get("reps", 1)),
        judge_model=(str(ev["judge_model"]) if ev.get("judge_model") else None),
        store=str(ev.get("store", "benchmark/eval.db")),
    )
```

- [ ] **Step 3b: Write the sweep orchestrator + matrix report**

```python
# backend/benchmark/agent/sweep.py
"""Run the variants × test-sets grid and report it (SRS §III-9).

Each cell (variant, bucket) is its own persisted experiment, keyed to
git_commit/stage/version/model + the bucket name. The matrix report puts
variants in columns and buckets in rows, with a Δ-vs-baseline column that
flags any bucket REGRESSION (⚠) — the "fix A breaks B/C" signal, made visible.
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
    variant: str            # full slot, e.g. 'router/v1'
    testset: str
    experiment_id: int
    mean_score: float | None
    mean_tokens_in: float | None


async def run_sweep(
    cfg: EvalConfig, *, adapter: Any, store_conn: Any,
    git_commit: str, created_at: str,
    count_tokens: Callable[[str, list[dict[str, str]]], int | None] | None = None,
) -> list[SweepCell]:
    spec = get_stage(cfg.stage)
    cells: list[SweepCell] = []
    for ts in cfg.testsets:
        corpus = corpus_mod.load_corpus(ts.corpus)
        for variant in cfg.variants:
            result = await run_experiment(
                spec, variant, corpus, adapter=adapter, model=cfg.model, reps=cfg.reps,
                judge_model=cfg.judge_model, count_tokens=count_tokens,
                git_commit=git_commit, created_at=created_at, corpus_name=ts.name,
                notes=f"sweep:{ts.name}",
            )
            meta, rows = to_store_payload(result)
            exp_id = store_mod.record_experiment(store_conn, meta=meta, scores=rows)
            cells.append(SweepCell(
                variant=result.meta.prompt_version, testset=ts.name,
                experiment_id=exp_id, mean_score=result.mean_score,
                mean_tokens_in=result.mean_tokens_in,
            ))
    return cells


def _fscore(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}"


def _ftok(x: float | None) -> str:
    return "—" if x is None else f"{x:.0f}"


def matrix_report(cfg: EvalConfig, cells: list[SweepCell]) -> str:
    spec = get_stage(cfg.stage)
    full = [f"{spec.slot_base}/{v}" for v in cfg.variants]
    baseline = full[0]
    by = {(c.testset, c.variant): c for c in cells}

    lines = [
        f"# Eval sweep: {cfg.stage}", "",
        f"- model: `{cfg.model}` · reps: {cfg.reps} · variants: {', '.join(full)}",
        "- cell = mean_score / mean_tokens_in · Δ = score vs baseline (⚠ = regression)", "",
    ]
    header = ["test set"] + [f"{v} (score/tok)" for v in full]
    header += [f"Δ {v} vs {baseline}" for v in full[1:]]
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

In `backend/benchmark/agent/cli.py`, add these imports next to the other `benchmark.agent` imports:

```python
from benchmark.agent.eval_config import load_eval_config
from benchmark.agent.sweep import matrix_report, run_sweep
```

Add the command handler (next to `_cmd_run`):

```python
def _cmd_sweep(args: argparse.Namespace) -> int:
    _load_env(args.env)
    cfg = load_eval_config(args.config)
    adapter = _make_adapter()
    conn = store.connect(cfg.store)
    cells = asyncio.run(run_sweep(
        cfg, adapter=adapter, store_conn=conn,
        git_commit=_git_commit(), created_at=datetime.now().isoformat(timespec="seconds"),
        count_tokens=_token_counter,
    ))
    report = matrix_report(cfg, cells)
    out = args.out or f"benchmark/results/{cfg.stage}-sweep-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWrote {out}")
    return 0
```

Register the subparser (between the `run` and `golden` parsers in `main`):

```python
    sw = sub.add_parser("sweep", help="run a variants x test-sets grid from a TOML config")
    sw.add_argument("--config", required=True)
    sw.add_argument("--out", default="")
    sw.add_argument("--env", default=".env")
    sw.set_defaults(fn=_cmd_sweep)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/benchmark_agent/test_sweep.py tests/benchmark_agent/test_cli.py -v`
Expected: PASS (test_sweep: 2 passed; test_cli still green).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run ruff check tests/benchmark_agent/test_sweep.py
git add benchmark/agent/eval_config.py benchmark/agent/sweep.py benchmark/agent/cli.py tests/benchmark_agent/test_sweep.py
git commit -m "feat(eval): config-driven sweep — variants x test-set buckets + matrix report"
```

---

## Task 10: Router corpus buckets, sweep config, README, and the real-API pilot gate

**Files:**
- Create: `backend/benchmark/agent/corpus/router.core.jsonl`
- Create: `backend/benchmark/agent/corpus/router.regression.jsonl`
- Create: `backend/benchmark/agent/corpus/router.edge.jsonl`
- Create: `backend/benchmark/agent/router.eval.toml`
- Create: `backend/benchmark/agent/README.md`

**Interfaces:** none (data + config + docs + the one-time real-API verification).

- [ ] **Step 1: Write the `core` bucket (target behaviour — one clean case per intent)**

Create `backend/benchmark/agent/corpus/router.core.jsonl` — one JSON object per line. `enabled_refs_count`/`slide_attached` reflect the realistic session state for each intent:

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

Create `backend/benchmark/agent/corpus/router.regression.jsonl` — the behaviours a router prompt change must NOT break (the no-ref rewrite-to-search rule, possessive→library_stats, language stability, named→search, on-screen-deck *question*→paper_qa). Per `writing-agent-prompts`: a change with no regression bucket is untested for side effects.

```jsonl
{"case_id": "reg-qa-noref-to-search", "stage": "router", "variables": {"user_message": "What does the transformer paper say about positional encodings?", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "paper_search"}, "rubric": "content question with NO refs attached -> router rewrites to paper_search", "source_run_id": null, "observed": null}
{"case_id": "reg-possessive-libstats", "stage": "router", "variables": {"user_message": "Show me the papers I already have on transformers", "enabled_refs_count": 2, "slide_attached": false}, "expect": {"intent": "library_stats"}, "rubric": "possessive 'papers I have' -> library_stats, NOT paper_suggest", "source_run_id": null, "observed": null}
{"case_id": "reg-language-stable", "stage": "router", "variables": {"user_message": "多頭注意力和單頭注意力有什麼不同?", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_qa"}, "rubric": "same content question in Traditional Chinese -> intent stays paper_qa (language must not change routing)", "source_run_id": null, "observed": null}
{"case_id": "reg-named-search-not-suggest", "stage": "router", "variables": {"user_message": "Search for the BERT paper", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "paper_search"}, "rubric": "named paper -> paper_search, NOT paper_suggest", "source_run_id": null, "observed": null}
{"case_id": "reg-onscreen-deck-qa", "stage": "router", "variables": {"user_message": "What is the second slide about?", "enabled_refs_count": 1, "slide_attached": true}, "expect": {"intent": "paper_qa"}, "rubric": "a QUESTION about the on-screen deck -> paper_qa (deck command vs deck question, v2.29)", "source_run_id": null, "observed": null}
```

- [ ] **Step 3: Write the `edge` bucket (ambiguous / short / anaphora)**

Create `backend/benchmark/agent/corpus/router.edge.jsonl` — the genuinely hard cases (these labels are debatable on purpose; the eval exists to surface them):

```jsonl
{"case_id": "edge-bare-followup-suggest", "stage": "router", "variables": {"user_message": "推薦幾篇", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_suggest"}, "rubric": "bare anaphoric follow-up 'recommend a few' -> paper_suggest (resolved against context)", "source_run_id": null, "observed": null}
{"case_id": "edge-one-word-slides", "stage": "router", "variables": {"user_message": "slides", "enabled_refs_count": 2, "slide_attached": false}, "expect": {"intent": "slides"}, "rubric": "one-word deck command -> slides", "source_run_id": null, "observed": null}
{"case_id": "edge-anaphora-qa", "stage": "router", "variables": {"user_message": "tell me more about this one", "enabled_refs_count": 1, "slide_attached": false}, "expect": {"intent": "paper_qa"}, "rubric": "anaphoric 'this one' with an enabled ref -> paper_qa", "source_run_id": null, "observed": null}
{"case_id": "edge-unresolvable-clarify", "stage": "router", "variables": {"user_message": "do the thing", "enabled_refs_count": 0, "slide_attached": false}, "expect": {"intent": "clarify"}, "rubric": "unresolvable with no refs and no antecedent -> clarify", "source_run_id": null, "observed": null}
```

- [ ] **Step 4: Write the sweep config**

Create `backend/benchmark/agent/router.eval.toml`:

```toml
# Per-agent eval sweep config (SRS §III-9): variants × test-set buckets.
# Add a prompt version by writing backend/src/paperhub/llm/prompts/router_vN.yaml
# then appending "vN" to `variants`; add a bucket by adding a [[testsets]] block
# (e.g. the harvested production-failures corpus).
[eval]
stage    = "router"
model    = "gemini/gemini-2.5-flash"
reps     = 3                         # variance — a delta within noise is not a win
store    = "benchmark/eval.db"
# judge_model is only used for stages WITHOUT a deterministic score; the router
# is graded by exact intent match, so it is omitted here.
variants = ["v1"]                    # add "v2" once router_v2.yaml exists

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

- [ ] **Step 5: Write the README**

Create `backend/benchmark/agent/README.md`:

```markdown
# Per-stage agent prompt evaluation (SRS §III-9, Plan G1)

Evaluate ONE agent prompt at a time, precisely, on real recorded inputs —
replay the stage with a prompt version, score its output (quality + token
count), and persist each run as a comparable experiment in a local `eval.db`.
In-process: it calls the LLM via the production adapter directly — **no live
backend needed**, only an LLM API key in `backend/.env`.

## The fast loop — sweep variants × test-set buckets (run from `backend/`)

`router.eval.toml` declares which prompt versions to compare and which test-set
buckets to run them on. One command runs the whole grid:

```powershell
# 1. (optional) harvest REAL inputs from the trace DB into a new bucket
scripts/run-eval.ps1 harvest --db workspace/paperhub.db --stage router `
  --out benchmark/agent/corpus/router.harvest.jsonl

# 2. write router_v2.yaml (built to the four principles), add "v2" to
#    router.eval.toml's `variants`, then sweep the whole grid:
scripts/run-eval.ps1 sweep --config benchmark/agent/router.eval.toml
```

The sweep prints + writes a matrix report — variants as columns, buckets as
rows, with a Δ-vs-baseline column flagging any **regression** (⚠):

```
# Eval sweep: router
- model: gemini-2.5-flash · reps: 3 · variants: router/v1, router/v2
| test set   | router/v1 (score/tok) | router/v2 (score/tok) | Δ router/v2 vs router/v1 |
|---|---|---|---|
| core       | 0.86 / 1180           | 0.95 / 910            | +0.09                    |
| regression | 1.00 / 1180           | 0.80 / 910            | -0.20 ⚠                  |
| edge       | 0.50 / 1180           | 0.75 / 910            | +0.25                    |
```

That ⚠ is the whole point: v2 is more concise and fixes edge cases but breaks a
behaviour the regression bucket guards — adopt nothing until it's clean.

## The primitives (what `sweep` orchestrates)

```powershell
# one prompt version × one bucket -> one experiment
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

- **Test-set buckets** — `core` (target behaviour), `regression` (other
  intents/languages the same prompt governs — the side-effect guard), `edge`
  (ambiguous/short/anaphora), and `harvest` (real production failures, promoted
  in). A change with no regression bucket is untested for side effects.
- **Variant** — a prompt version = a `router_vN.yaml` file (authored by you or
  Claude). The config lists which to compare.
- **Replay** — re-render only this stage's prompt from the recorded input state
  and call the model. The prompt is the only variable.
- **Two scores** — output quality (`mean_score`, 0..1; deterministic intent match
  for the router, judge otherwise) + prompt quality (`mean_tokens_in`).
- **Reps** — run each case N times for variance, so a delta is signal not noise.
- **Freeze + propagate** — a frozen winner's golden outputs become the next
  stage's real input set. The cascade rolls down the tree from the router.

`eval.db` + `*.harvest.jsonl` are gitignored; the `core`/`regression`/`edge`
buckets + `router.eval.toml` are committed. See SRS §III-9 for the full
methodology and `writing-agent-prompts` for the variant→queries→judge
discipline this automates.
```

- [ ] **Step 6: Run the new test suite + commit the data/config/docs**

Run: `cd backend; uv run pytest tests/benchmark_agent/ -v; uv run ruff check tests/benchmark_agent`
Expected: all green; ruff clean.

```bash
cd backend
git add benchmark/agent/corpus/router.core.jsonl benchmark/agent/corpus/router.regression.jsonl \
        benchmark/agent/corpus/router.edge.jsonl benchmark/agent/router.eval.toml \
        benchmark/agent/README.md
git commit -m "docs(eval): bucketed router corpora + sweep config + README"
```

- [ ] **Step 7: Real-API pilot gate (run ONCE — the plan-phase verification)**

This is the correctness gate the unit tests cannot give (per CLAUDE.md "pytest is necessary-but-insufficient"). It needs an LLM API key in `backend/.env` (it does NOT need the `:8000` backend — replay is in-process).

```powershell
cd backend
# Baseline the SHIPPED router prompt (v1) across all three buckets in one sweep.
scripts/run-eval.ps1 sweep --config benchmark/agent/router.eval.toml
scripts/run-eval.ps1 list --stage router
```

Confirm:
1. The sweep prints a matrix report with rows `core`/`regression`/`edge` and a `router/v1 (score/tok)` column, and writes it under `benchmark/results/`.
2. `scripts/run-eval.ps1 list --stage router` shows **three** experiments (one per bucket) keyed to the git commit + `router/v1`.
3. Per-bucket rows: `SELECT case_id, score, tokens_in FROM eval_scores WHERE experiment_id = <id>;` — every case scored (1.0 correct intent / 0.0 miss). Any miss points at genuinely-debatable router behaviour (a real finding, not a harness bug).

If a case the router *should* get is scored 0, that is exactly the signal this system exists to surface — note it as the first candidate for a `router_v2.yaml` rewrite (a separate `writing-agent-prompts` task: add `"v2"` to `router.eval.toml` and re-sweep to compare; out of scope for G1's machinery).

- [ ] **Step 8: Full backend quality gates (plan-phase completion)**

Run: `cd backend; uv run pytest -q; uv run ruff check src tests; uv run mypy src`
Expected: all green (the new tests added; the one `src` change typechecks).

---

## Self-Review

**Spec coverage (SRS §III-9 → tasks):**
- "Real-input corpus, never synthesized; failed-run promotion" → Task 3 (`harvest`), Task 10 (bucketed corpora: core/regression/edge + the gitignored harvest bucket). ✓
- "Per-stage replay harness, in-process, prompt is the only variable" → Task 4 (`replay_stage` + `build_messages`). ✓
- "≥2 variants × corpus × judge; N-run variance; pairwise" → Task 6 (`reps`), Task 5 (`judge_scalar`/`judge_pairwise`); the variant-vs-variant comparison is the config-driven `sweep` (Task 9), or the `run`+`compare` primitives (Task 8). ✓
- "Configurable prompt-set × test-sets (the human/Claude-editable eval declaration)" → Task 9 (`eval_config.py` + `sweep.py` + `sweep` CLI verb + matrix report) over the bucketed test sets of Task 10. ✓
- "Two scores: output quality + token count" → Task 5 (`score`) + Task 4 (`tokens_in`), aggregated in Task 6 and stored in Task 1. ✓
- "Freeze + propagate golden outputs" → Task 7 (`emit_golden`), CLI `golden` (Task 8). ✓
- "Local-first eval.db keyed to {git_commit, stage, prompt_version, model}; CLI harvest|run|sweep|compare|list|golden" → Task 1 (schema) + Task 8 (5 primitive verbs) + Task 9 (`sweep`). ✓
- "Reuse judge.py + tracer + writing-agent-prompts" → Task 5 reuses judge discipline + `load_env`; Task 3 reads `tool_calls`; README points at the skill. ✓
- Acceptance I-8 #7 (replay in isolation, judged variant-vs-baseline with variance, persisted experiment keyed to commit/stage/version/model) → Tasks 4–9. ✓
- Deferred by design (noted, not built in G1): LangSmith `--export`; downstream stages beyond router; four-principle-adherence judge (token count is the G1 prompt-quality metric). Consistent with the §III-9 "Phase-1 + router pilot" scope.

**Placeholder scan:** every code step contains complete, runnable code; every test asserts concrete values; no "TBD"/"handle edge cases"/"similar to Task N". ✓

**Type consistency:** `CorpusCase`, `StageSpec`, `ReplayOutput`, `CaseScore`, `ExperimentMeta`/`ExperimentResult`, `EvalConfig`/`TestSet`/`SweepCell` field names match across Tasks 2–10; `replay_stage`/`score_case`/`run_experiment`/`to_store_payload`/`record_experiment`/`run_sweep`/`matrix_report` signatures are used identically by their callers; `build_messages` matches the stub in Tasks 4/9's tests and the seam in Task 8. ✓
