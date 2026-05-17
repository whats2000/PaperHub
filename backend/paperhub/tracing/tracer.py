"""Tool-Call Tracer — single source of truth for FR-11 + FR-12.

Per design §6 persistence model, each call commits its own short
transaction *before* the corresponding SSE event is emitted (Task 7 wires
the SSE part). This module gives agents one synchronous `record(...)`
method; the LangGraph context-manager / decorator wrappers land in Task 6.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from paperhub.data.db import connect
from paperhub.data.models import ToolCallStatus
from paperhub.tracing.redactor import redact


class ToolCallTracer:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def record(
        self,
        *,
        run_id: UUID,
        step_index: int,
        parent_step: int | None,
        agent: str,
        tool: str,
        model: str | None,
        args: dict[str, object],
        result_summary: dict[str, object] | None,
        latency_ms: int,
        token_in: int | None,
        token_out: int | None,
        status: ToolCallStatus,
        error: str | None,
    ) -> None:
        redacted_args = redact(args)
        with connect(self._db_path) as conn:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "INSERT INTO tool_calls("
                    "  run_id, step_index, parent_step, agent, tool, model,"
                    "  args_redacted_json, result_summary_json,"
                    "  latency_ms, token_in, token_out, status, error"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(run_id),
                        step_index,
                        parent_step,
                        agent,
                        tool,
                        model,
                        json.dumps(redacted_args, sort_keys=True),
                        json.dumps(result_summary, sort_keys=True)
                        if result_summary is not None
                        else None,
                        latency_ms,
                        token_in,
                        token_out,
                        status,
                        error,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
