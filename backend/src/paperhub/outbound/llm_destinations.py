"""Destination-only audit rows for calls to a non-local LLM provider (H5a).

Prompt and response content is never logged: the row names the provider and
says so. One row per (run, model); outside a run at most one per model per
``_IDLE_WINDOW_S``. A logging failure never fails the LLM call. Every direct
``litellm.acompletion`` caller goes through :func:`acompletion_noted`.
"""
from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from typing import Any

import litellm

from paperhub.outbound import log
from paperhub.outbound.context import outbound_scope
from paperhub.outbound.local_models import is_local_model
from paperhub.outbound.policy import load_policy

logger = logging.getLogger(__name__)

_IDLE_WINDOW_S = 600.0
_MAX_SEEN = 1000
_seen_runs: OrderedDict[tuple[int, str], None] = OrderedDict()
_seen_idle: dict[str, float] = {}


def reset_for_tests() -> None:
    _seen_runs.clear()
    _seen_idle.clear()


async def note_llm_destination(model: str) -> None:
    try:
        if is_local_model(model, os.environ) or not load_policy().review_enabled:
            return
        scope = outbound_scope.get()
        key = (scope.run_id, model) if scope is not None else None
        now = time.monotonic()
        if key is not None:
            if key in _seen_runs:
                return
            _seen_runs[key] = None  # reserve so concurrent calls log once
            while len(_seen_runs) > _MAX_SEEN:
                _seen_runs.popitem(last=False)
        else:
            last = _seen_idle.get(model)
            if last is not None and now - last < _IDLE_WINDOW_S:
                return
            _seen_idle[model] = now
        try:
            await log.append(
                None,
                actor="agent" if scope is not None else "system",
                caller="llm", kind="llm",
                destination=model.partition("/")[0] if "/" in model else model,
                decision="sent", stage="llm",
                sent_text="(content not logged)",
                run_id=scope.run_id if scope else None,
                session_id=scope.session_id if scope else None,
            )
        except BaseException:
            # A failed append must be retryable by the next call.
            if key is not None:
                _seen_runs.pop(key, None)
            else:
                _seen_idle.pop(model, None)
            raise
    except Exception:  # noqa: BLE001 - auditing must never fail the call
        logger.warning("outbound: LLM destination log failed", exc_info=True)


async def acompletion_noted(**kwargs: Any) -> Any:
    """``litellm.acompletion`` that also notes the destination (looked up at
    call time, so tests patching ``litellm.acompletion`` keep working). The
    row is written even when the call raises: the request left the device."""
    try:
        return await litellm.acompletion(**kwargs)
    finally:
        await note_llm_destination(str(kwargs.get("model", "")))
