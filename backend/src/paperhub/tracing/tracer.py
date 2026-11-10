import asyncio
import contextvars
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from paperhub.db.connection import in_write_transaction, write_lock
from paperhub.models.domain import Branch
from paperhub.tracing.redactor import redact


@dataclass
class _StepBuffer:
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    token_in: int | None = None
    token_out: int | None = None
    forced_status: str | None = None
    forced_error: str | None = None
    # Model downgrades recorded DURING the step (e.g. the LLM adapter falling
    # back from an unavailable flagship to the small tier). Folded into the
    # written result as ``_model_fallbacks`` so the trace shows "flagship
    # unavailable → used <small>" without the caller threading anything.
    model_fallbacks: list[dict[str, str]] = field(default_factory=list)
    # Token usage + latency of every LLM call made DURING the step (the LLM
    # adapter notes each one). Folded into the result as ``_llm_usage``; the
    # token columns sum them unless the caller recorded its own counts.
    llm_usage: list[dict[str, Any]] = field(default_factory=list)

    def record_args(self, args: dict[str, Any]) -> None:
        self.args = args

    def record_result(self, result: dict[str, Any]) -> None:
        self.result = result

    def record_tokens(self, *, token_in: int | None, token_out: int | None) -> None:
        self.token_in = token_in
        self.token_out = token_out

    def mark_error(self, message: str) -> None:
        """Record that the step is logically failed even if no exception
        propagates out of the ``with`` block. Used by callers that catch
        exceptions from a dispatched tool but still want the trace to
        show the failure."""
        self.forced_error = message

    def mark_rejected(self, message: str) -> None:
        """Force status='rejected' (NFR-05 scope boundary). Distinct from
        mark_error: a rejection is a deliberate policy stop, not a fault."""
        self.forced_status = "rejected"
        self.forced_error = message


# The trace step currently in scope on THIS task. Set by ``Tracer.step`` around
# its ``yield`` so code called inside the ``async with`` block (notably the LLM
# adapter) can annotate the step WITHOUT the caller threading the buffer through.
# Same-task only — an LLM call awaited inside a node's step shares this context.
# (This is the in-task case; the cross-task MCP case in CLAUDE.md is different —
# there a ContextVar can't reach the caller, so MCP uses the _meta payload.)
_CURRENT_STEP: contextvars.ContextVar[_StepBuffer | None] = contextvars.ContextVar(
    "paperhub_current_step", default=None,
)


def note_model_fallback(from_model: str, to_model: str, reason: str) -> None:
    """Record a model downgrade onto the currently-active trace step.

    Safe no-op when there is no active step (the LLM call happens outside any
    ``tracer.step``). Used by the adapter when a flagship call is unavailable
    and it downgrades to the small tier — so the trace shows the degrade."""
    buf = _CURRENT_STEP.get()
    if buf is not None:
        buf.model_fallbacks.append(
            {"from": from_model, "to": to_model, "reason": reason},
        )


def note_llm_usage(model: str, usage: Any, latency_ms: int) -> None:
    """Record one LLM call's token usage onto the currently-active trace step.

    ``usage`` is the provider response's ``usage`` (an object or a dict, as
    litellm returns it); ``reasoning_tokens`` is read from its
    ``completion_tokens_details``. Safe no-op outside any ``tracer.step``."""
    buf = _CURRENT_STEP.get()
    if buf is None:
        return

    def get(obj: Any, key: str) -> Any:
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

    def as_int(v: Any) -> int | None:
        return int(v) if isinstance(v, (int, float)) else None

    details = get(usage, "completion_tokens_details") if usage is not None else None
    buf.llm_usage.append({
        "model": model,
        "prompt_tokens": as_int(get(usage, "prompt_tokens")) if usage is not None else None,
        "completion_tokens": (as_int(get(usage, "completion_tokens"))
                              if usage is not None else None),
        "reasoning_tokens": as_int(get(details, "reasoning_tokens")) if details else None,
        "latency_ms": latency_ms,
    })


def _sum_known(values: Any) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


class Tracer:
    def __init__(
        self, conn: aiosqlite.Connection, *, run_id: int, branch: Branch,
        append_only: bool = False,
    ) -> None:
        self._conn = conn
        self._run_id = run_id
        self._branch = branch
        self._next_index = 0
        # Tracers that join an already-running run (e.g. a user-confirmed
        # action) take the next free index at WRITE time, so they can never
        # collide with the run's own rows.
        self._append_only = append_only

    @property
    def run_id(self) -> int:
        """Public accessor for the run ID."""
        return self._run_id

    @property
    def connection(self) -> aiosqlite.Connection:
        """Public accessor for the underlying DB connection."""
        return self._conn

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
        # Expose THIS step to code awaited inside the block (the LLM adapter)
        # so a model downgrade annotates it; reset on exit (same task/context).
        cv_token = _CURRENT_STEP.set(buf)
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
            if buf.forced_status is not None:
                status, error = buf.forced_status, buf.forced_error
            elif buf.forced_error is not None:
                status, error = "error", buf.forced_error
            await self._write(buf, index, agent, tool, model, parent_step,
                              started, status, error)
        finally:
            _CURRENT_STEP.reset(cv_token)

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
        # Fold any model-downgrade notes into the result so the trace records
        # "flagship unavailable → small" even when the step had no other result.
        result_payload = buf.result
        if buf.model_fallbacks:
            result_payload = {**(buf.result or {}), "_model_fallbacks": buf.model_fallbacks}
        token_in, token_out = buf.token_in, buf.token_out
        if buf.llm_usage:
            result_payload = {**(result_payload or {}), "_llm_usage": buf.llm_usage}
            if token_in is None and token_out is None:
                token_in = _sum_known(u["prompt_tokens"] for u in buf.llm_usage)
                token_out = _sum_known(u["completion_tokens"] for u in buf.llm_usage)
        result_json = (
            json.dumps(redact(result_payload)) if result_payload is not None else None
        )
        params = {
            "run_id": self._run_id,
            "branch": self._branch,
            "step_index": index,
            "parent_step": parent_step,
            "agent": agent,
            "tool": tool,
            "model": model,
            "args_redacted_json": args_json,
            "result_summary_json": result_json,
            "latency_ms": latency_ms,
            "token_in": token_in,
            "token_out": token_out,
            "status": status,
            "error": error,
        }
        next_free = (
            "(SELECT COALESCE(MAX(step_index), -1) + 1 FROM tool_calls "
            "WHERE run_id = :run_id AND branch = :branch)"
        )

        def insert(step_expr: str) -> str:
            return (
                "INSERT INTO tool_calls (run_id, branch, step_index, parent_step, "
                "agent, tool, model, args_redacted_json, result_summary_json, "
                "latency_ms, token_in, token_out, status, error) "
                f"VALUES (:run_id, :branch, {step_expr}, :parent_step, "
                ":agent, :tool, :model, :args_redacted_json, :result_summary_json, "
                ":latency_ms, :token_in, :token_out, :status, :error)"
            )

        # Serialised with every other writer on this connection (ruling R9):
        # concurrent tasks share it, so an unlocked INSERT + commit could
        # land inside, and end, a sibling's open write transaction. Inside
        # the caller's own write_transaction the row joins that transaction
        # and its commit, instead of committing it early.
        async with write_lock(self._conn):
            if self._append_only:
                await self._conn.execute(insert(next_free), params)
            else:
                try:
                    await self._conn.execute(insert(":step_index"), params)
                except sqlite3.IntegrityError:
                    # A step appended by a user-confirmed action took this index.
                    await self._conn.execute(insert(next_free), params)
            if not in_write_transaction():
                await self._conn.commit()
