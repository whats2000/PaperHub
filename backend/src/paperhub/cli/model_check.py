"""``paperhub-model-check``: can the configured models run PaperHub?

Per model, four checks the agent relies on: structured output, two-step tool
calling, long context, and vision. Prints pass/fail with the error; exit code
1 when any check fails. Costs real (or local) model calls: run it on demand.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import sys
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel

from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.llm.prompts.registry import PromptRegistry, PromptSlot
from paperhub.outbound.local_models import configured_models

CHECKS = ("structured", "tools", "context", "vision")
_SLOT = "model_check/v1"
_NEEDLE = "The vault code is 48213."
_FONT = {
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
}


@dataclass
class CheckResult:
    model: str
    check: str
    ok: bool
    error: str | None = None


class _Person(BaseModel):
    name: str
    age: int


class _CheckRegistry(PromptRegistry):
    def get(self, slot: str) -> PromptSlot:
        return PromptSlot(system="Answer exactly as asked.", user_template="{text}")


def digit_png(digit: str = "7", size: int = 64, scale: int = 6) -> bytes:
    """A ``size``x``size`` greyscale PNG with one large dark digit (stdlib only)."""
    img = [[255] * size for _ in range(size)]
    ox, oy = (size - 5 * scale) // 2, (size - 7 * scale) // 2
    for y, row in enumerate(_FONT[digit].split()):
        for x, bit in enumerate(row):
            if bit == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        img[oy + y * scale + dy][ox + x * scale + dx] = 0

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes(r) for r in img)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def _structured(ad: LlmAdapter, model: str, _ctx: int) -> None:
    out = await ad.structured(
        slot=_SLOT, variables={"text": "Alice is 30 years old. Extract name and age."},
        response_model=_Person, model=model, fallback_model="")
    if (out.name.strip().lower(), out.age) != ("alice", 30):
        raise AssertionError(f"wrong fields: {out!r}")


_TOOLS = [
    {"type": "function", "function": {
        "name": "get_code", "description": "Returns the secret code.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "use_code", "description": "Submits the secret code.",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}},
                       "required": ["code"]}}},
]


async def _tools(ad: LlmAdapter, model: str, _ctx: int) -> None:
    msgs: list[dict[str, Any]] = [{"role": "user", "content":
        "Call get_code, then call use_code with the code it returns."}]
    t1 = await ad.tool_turn(messages=msgs, tools=_TOOLS, model=model)
    if not t1.tool_calls or t1.tool_calls[0].name != "get_code":
        raise AssertionError("first turn did not call get_code")
    c = t1.tool_calls[0]
    msgs.append({"role": "assistant", "content": t1.content, "tool_calls": [{
        "id": c.id, "type": "function",
        "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}]})
    msgs.append({"role": "tool", "tool_call_id": c.id, "content": "K-17"})
    t2 = await ad.tool_turn(messages=msgs, tools=_TOOLS, model=model)
    call = next((x for x in t2.tool_calls if x.name == "use_code"), None)
    if call is None:
        raise AssertionError("second turn did not call use_code")
    if "K-17" not in json.dumps(call.arguments):
        raise AssertionError(f"tool A's result did not reach tool B: {call.arguments}")


async def _context(ad: LlmAdapter, model: str, ctx: int) -> None:
    filler = "The sky was clear and the market was quiet that day. "
    n = max(1, int(ctx * 3.5 / len(filler)) // 2)
    text = filler * n + _NEEDLE + " " + filler * n
    turn = await ad.tool_turn(
        messages=[{"role": "user", "content": text + "\n\nWhat is the vault code?"}],
        tools=[], model=model, max_tokens=256)
    if "48213" not in turn.content:
        raise AssertionError(f"needle not recalled: {turn.content[:80]!r}")


async def _vision(ad: LlmAdapter, model: str, _ctx: int) -> None:
    b64 = base64.b64encode(digit_png("7")).decode()
    turn = await ad.tool_turn(
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Which digit is in the image? Answer with the digit only."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}],
        tools=[], model=model, max_tokens=256)
    if "7" not in turn.content:
        raise AssertionError(f"digit not read: {turn.content[:80]!r}")


_RUNNERS: dict[str, Callable[[LlmAdapter, str, int], Awaitable[None]]] = {
    "structured": _structured, "tools": _tools, "context": _context, "vision": _vision}


async def run_checks(adapter: LlmAdapter, models: list[str],
                     context_tokens: int) -> list[CheckResult]:
    results: list[CheckResult] = []
    for model in models:
        for name in CHECKS:
            try:
                await _RUNNERS[name](adapter, model, context_tokens)
                results.append(CheckResult(model, name, True))
            except Exception as exc:  # noqa: BLE001 - every failure is a result
                results.append(CheckResult(model, name, False,
                                           f"{type(exc).__name__}: {exc}"[:300]))
    return results


def format_report(results: list[CheckResult]) -> str:
    lines = [f"{'PASS' if r.ok else 'FAIL'}  {r.model}  {r.check}"
             + (f"  -- {r.error}" if r.error else "") for r in results]
    failed = [r for r in results if not r.ok]
    summary = f"{len(results) - len(failed)}/{len(results)} checks passed"
    if failed:
        summary += "; failing: " + ", ".join(f"{r.model}:{r.check}" for r in failed)
    return "\n".join([*lines, summary])


def main(argv: list[str] | None = None, adapter: LlmAdapter | None = None) -> int:
    p = argparse.ArgumentParser(description="Check configured models against PaperHub's needs")
    p.add_argument("--model", help="check only this model (default: every configured model)")
    p.add_argument("--context", type=int, default=32000, help="context-check size in tokens")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)
    models = [args.model] if args.model else list(dict.fromkeys(
        m for _, m in configured_models() if m))
    ad = adapter or LiteLlmAdapter(_CheckRegistry(), fallback_model="")
    results = asyncio.run(run_checks(ad, models, args.context))
    print(json.dumps([asdict(r) for r in results], indent=2) if args.json
          else format_report(results))
    return 0 if all(r.ok for r in results) else 1


def _entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entry()
