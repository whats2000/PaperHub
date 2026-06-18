"""Live abort/resume verification against a running backend on :8000 (FR-15).

Proves the load-bearing Stop properties end-to-end against the REAL LLM stack
(pytest cannot — it stubs the adapter):

  (2) Abort makes NO more model call  — `tool_calls` for the run is frozen after
      the cancel settles (no new agent/LLM steps), and zero token events arrive
      after the cancel.
  (3) Pair invariant on the cancel path — the run's user message is DELETED (no
      orphaned user message left without a response) and the run is 'cancelled'.

Run:  cd backend ; uv run --no-sync python scripts/live_resume_test.py
Requires the user's backend live on :8000 and reads workspace/paperhub.db (RO).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
DB = "workspace/paperhub.db"
# A multi-stage paper_suggest turn fires several agent/LLM steps (Parser →
# Processor → Finalizer → Synthesizer), giving a wide mid-generation window.
PROMPT = "Recommend a few recent papers about diffusion models for protein design."


def _q1(sql: str, args: tuple[object, ...]) -> object:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=4000")
    try:
        row = con.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def tool_calls_count(run_id: int) -> int:
    return int(_q1("SELECT COUNT(*) FROM tool_calls WHERE run_id=?", (run_id,)) or 0)


def run_status(run_id: int) -> str | None:
    v = _q1("SELECT status FROM runs WHERE id=?", (run_id,))
    return str(v) if v is not None else None


def msg_count(run_id: int) -> int:
    return int(_q1("SELECT COUNT(*) FROM messages WHERE run_id=?", (run_id,)) or 0)


async def main() -> int:
    async with httpx.AsyncClient(timeout=None) as client:
        sid = (await client.post(f"{BASE}/sessions")).json()["session_id"]
        print(f"session_id={sid}")

        state: dict[str, object] = {"run_id": None, "cancel_ts": None, "tokens_after_cancel": 0}

        async def consume() -> None:
            body = {"session_id": sid, "user_message": PROMPT, "history": [], "slide_attached": False}
            event = None
            async with client.stream("POST", f"{BASE}/chat", json=body) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        if event == "session":
                            state["run_id"] = json.loads(data)["run_id"]
                            print(f"run_id={state['run_id']}")
                        elif event == "token" and state["cancel_ts"] is not None:
                            state["tokens_after_cancel"] = int(state["tokens_after_cancel"]) + 1

        task = asyncio.create_task(consume())

        # Wait until the agent has actually started doing work (>=1 tool_call).
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            rid = state["run_id"]
            if isinstance(rid, int) and tool_calls_count(rid) >= 1:
                break
            await asyncio.sleep(0.5)
        rid = state["run_id"]
        if not isinstance(rid, int):
            print("FAIL: no run_id / no tool_calls within 40s")
            task.cancel()
            return 1

        await asyncio.sleep(1.0)  # let it be mid-generation
        before = tool_calls_count(rid)
        state["cancel_ts"] = time.monotonic()
        ack = (await client.post(f"{BASE}/chat/cancel", json={"run_id": rid})).json()
        print(f"cancel ack={ack}; tool_calls at cancel={before}")

        # Let the in-flight step (if any) finalize, then prove NO new steps fire.
        await asyncio.sleep(2.5)
        settled = tool_calls_count(rid)
        await asyncio.sleep(5.0)
        after = tool_calls_count(rid)

        try:
            await asyncio.wait_for(task, timeout=3)
        except (TimeoutError, asyncio.TimeoutError):
            task.cancel()

        status = run_status(rid)
        msgs = msg_count(rid)
        tac = int(state["tokens_after_cancel"])

        print("\n--- RESULTS ---")
        print(f"(2a) tokens after cancel: {tac}            (expect 0)")
        print(f"(2b) tool_calls settled={settled} then +5s={after}  (expect equal → no new model calls)")
        print(f"(3)  run status={status!r}                 (expect 'cancelled')")
        print(f"(3)  messages left for run={msgs}          (expect 0 → no orphan user message)")

        ok = (tac == 0) and (after == settled) and (status == "cancelled") and (msgs == 0)
        print("\nVERDICT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
