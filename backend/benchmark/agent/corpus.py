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
