import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import aiosqlite

from paperhub.models.domain import Branch
from paperhub.tracing.redactor import redact


@dataclass
class _StepBuffer:
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    token_in: int | None = None
    token_out: int | None = None

    def record_args(self, args: dict[str, Any]) -> None:
        self.args = args

    def record_result(self, result: dict[str, Any]) -> None:
        self.result = result

    def record_tokens(self, *, token_in: int | None, token_out: int | None) -> None:
        self.token_in = token_in
        self.token_out = token_out


class Tracer:
    def __init__(self, conn: aiosqlite.Connection, *, run_id: int, branch: Branch) -> None:
        self._conn = conn
        self._run_id = run_id
        self._branch = branch
        self._next_index = 0

    @asynccontextmanager
    async def step(
        self,
        *,
        agent: str,
        tool: str,
        model: str | None,
        parent_step: int | None = None,
    ) -> AsyncIterator[_StepBuffer]:
        buf = _StepBuffer()
        index = self._next_index
        self._next_index += 1
        started = time.monotonic()
        status: str = "ok"
        error: str | None = None
        try:
            yield buf
        except asyncio.CancelledError:
            status, error = "error", "cancelled"
            await self._write(buf, index, agent, tool, model, parent_step,
                              started, status, error)
            raise
        except Exception as exc:
            status, error = "error", str(exc)
            await self._write(buf, index, agent, tool, model, parent_step,
                              started, status, error)
            raise
        else:
            await self._write(buf, index, agent, tool, model, parent_step,
                              started, status, error)

    async def _write(
        self,
        buf: _StepBuffer,
        index: int,
        agent: str,
        tool: str,
        model: str | None,
        parent_step: int | None,
        started: float,
        status: str,
        error: str | None,
    ) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        args_json = json.dumps(redact(buf.args)) if buf.args is not None else None
        result_json = json.dumps(redact(buf.result)) if buf.result is not None else None
        await self._conn.execute(
            "INSERT INTO tool_calls (run_id, branch, step_index, parent_step, "
            "agent, tool, model, args_redacted_json, result_summary_json, "
            "latency_ms, token_in, token_out, status, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._run_id, self._branch, index, parent_step,
             agent, tool, model, args_json, result_json,
             latency_ms, buf.token_in, buf.token_out, status, error),
        )
        await self._conn.commit()
