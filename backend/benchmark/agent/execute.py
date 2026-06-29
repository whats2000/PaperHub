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
import os
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

    fd, tmp_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    tmp = Path(tmp_path)
    tmp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")  # noqa: ASYNC240
    try:
        with tmp.open("rb") as fh:  # noqa: ASYNC230
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
        tmp.unlink(missing_ok=True)  # noqa: ASYNC240

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
