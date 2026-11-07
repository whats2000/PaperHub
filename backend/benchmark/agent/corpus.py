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

from pydantic import ValidationError

from benchmark.agent.stages import StageSpec, get_stage


@dataclass
class CorpusCase:
    case_id: str
    stage: str
    variables: dict[str, Any]
    expect: dict[str, Any]
    rubric: str = ""
    source_run_id: int | None = None
    observed: dict[str, Any] | None = field(default=None)
    history: list[dict[str, str]] = field(default_factory=list)


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
        if run_ids is not None:
            if not run_ids:
                return []
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
        if isinstance(observed, dict) and "skipped" in observed:
            continue  # the step never called the model (e.g. a blank answer)
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
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            out.append(CorpusCase(**json.loads(line)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"{path}: malformed corpus line {lineno}: {exc}") from exc
    return out


async def emit_golden(
    spec: StageSpec, version: str, corpus: list[CorpusCase], *,
    model: str, prompts_dir: Any, backend: str = "auto", count_tokens: Any = None,
) -> list[dict[str, Any]]:
    """Run the FROZEN winning variant over the corpus and return its golden
    outputs — the cascade hinge (SRS §III-9): these become the next stage's real
    input set, never synthesized."""
    out: list[dict[str, Any]] = []
    for case in corpus:
        r = await replay_stage(spec, version, case, model=model, prompts_dir=prompts_dir,
                               backend=backend, count_tokens=count_tokens)
        out.append({"case_id": case.case_id, "source_run_id": case.source_run_id,
                    "variables": case.variables, "output": r.output})
    return out


from benchmark.agent.replay import replay_stage  # noqa: E402, I001 — bottom import breaks the import cycle


def import_inbox(
    inbox: str | Path, stage: str,
) -> tuple[list[CorpusCase], int, int]:
    """Promote the HUMAN-LABELLED lines of the H2 feedback inbox to corpus cases.

    A line is labelled once its ``expect`` is non-empty; unlabelled (or
    other-stage) lines are skipped as unlabelled; a labelled line whose JSON or
    ``variables`` is unusable is skipped as invalid. Returns
    ``(cases, unlabelled, invalid)``. Note: inbox lines of any ``stage_hint``
    carry the ROUTER step's variables/observed, so the importer filters by
    stage; only a router import is meaningful today.
    ``variables`` is normalised through the stage's own extractor so a recorded
    router args dict becomes exactly what the harness replays."""
    spec = get_stage(stage)
    cases: list[CorpusCase] = []
    unlabelled = invalid = 0
    for raw in Path(inbox).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            expect = row.get("expect")
            hint = row.get("stage_hint")
            if not expect or (hint and hint != stage):
                unlabelled += 1
                continue
            variables = spec.variables_from_args(row.get("variables") or {})
            cases.append(CorpusCase(
                case_id=str(row["case_id"]), stage=stage, variables=variables,
                expect=dict(expect), rubric=str(row.get("correction") or ""),
                source_run_id=row.get("source_run_id"),
                observed=row.get("observed") or None))
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            invalid += 1
    return cases, unlabelled, invalid


def merge_corpus(path: str | Path, cases: list[CorpusCase]) -> tuple[int, int]:
    """Write ``cases`` into ``path`` WITHOUT clobbering it: a same-``case_id``
    line is replaced, every other existing line is kept. Returns
    ``(added, replaced)``."""
    existing = load_corpus(path) if Path(path).exists() else []
    new_ids = {c.case_id for c in cases}
    old_ids = {c.case_id for c in existing}
    save_corpus(path, [c for c in existing if c.case_id not in new_ids] + cases)
    return len(new_ids - old_ids), len(new_ids & old_ids)


def _step_target(spec: StageSpec, observed: dict[str, Any]) -> dict[str, Any]:
    """The model-output part of a recorded step result: validated through the
    stage's response model (extra trace keys dropped, unrecorded defaults not
    invented), else its ``output_summary``. Raises on an invalid output."""
    if spec.response_model is not None:
        return spec.response_model.model_validate(observed).model_dump(exclude_unset=True)
    return spec.output_summary(observed)


def export_steps(
    db_path: str | Path, stage: str, *, version: str, prompts_dir: str | Path,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Agent-step ``messages`` samples reconstructed from RECORDED state: the
    stage's prompt variant rendered over each run's recorded step input, with
    the recorded output as the assistant turn (never a stored prompt).

    The assistant turn is the recorded result narrowed to the stage's output
    (``_step_target``) — the model's own output fields only, not the trace
    bookkeeping a step records beside it (e.g. a review's ``guessed_reasons``
    / ids / ``feedback_id``). A step with no recorded output, or one that does
    not validate as the stage's output, has no target and is dropped."""
    from benchmark.agent.prompts import load_variant
    from benchmark.agent.replay import render_messages

    spec = get_stage(stage)
    system, user = load_variant(stage, version, prompts_dir=prompts_dir)
    out: list[dict[str, Any]] = []
    for case in harvest(db_path, stage, limit=limit):
        if not case.observed:
            continue
        try:
            target = _step_target(spec, case.observed)
        except (ValidationError, TypeError, KeyError):
            continue
        messages = render_messages(system, user, case.variables, case.history)
        messages.append({"role": "assistant",
                         "content": json.dumps(target, ensure_ascii=False)})
        out.append({"messages": messages, "source_run_id": case.source_run_id})
    return out
