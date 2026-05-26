"""Ad-hoc real-API eval driver for PaperHub (uncommitted benchmark harness).

Drives the user's live backend on :8000 exactly as the frontend would:
  POST /sessions  -> session_id
  POST /papers    -> attach a reference (library:<pc_id>, cheap dedup hit)
  POST /chat      -> stream SSE, collect routing intent / tokens / deck / run_id

Run with:  uv run python benchmark/driver.py  (smoke test)
Imported by run_eval.py for the full 20-case sweep.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"


@dataclass
class ChatResult:
    run_id: int | None = None
    session_id: int | None = None
    intent: str | None = None
    routing: dict[str, Any] | None = None
    final: str = ""
    deck: dict[str, Any] | None = None
    search_results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    events: list[str] = field(default_factory=list)


def create_session() -> int:
    r = httpx.post(f"{BASE}/sessions", timeout=30)
    r.raise_for_status()
    return int(r.json()["session_id"])


def add_paper(session_id: int, pc_id: int) -> dict[str, Any]:
    """Attach an already-ingested paper_content row to the session (dedup hit)."""
    r = httpx.post(
        f"{BASE}/papers",
        json={"session_id": session_id, "paper_id": f"library:{pc_id}"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def _parse_sse(text: str) -> list[tuple[str, str]]:
    """Yield (event, data) pairs from a raw SSE byte stream."""
    out: list[tuple[str, str]] = []
    event = "message"
    data_lines: list[str] = []
    for line in text.splitlines():
        if line == "":
            if data_lines:
                out.append((event, "\n".join(data_lines)))
            event = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if data_lines:
        out.append((event, "\n".join(data_lines)))
    return out


def chat(
    session_id: int,
    user_message: str,
    *,
    current_view_page: int = 0,
    timeout: float = 1800.0,
) -> ChatResult:
    res = ChatResult(session_id=session_id)
    tokens: list[str] = []
    payload = {
        "session_id": session_id,
        "user_message": user_message,
        "current_view_page": current_view_page,
    }
    with httpx.stream(
        "POST", f"{BASE}/chat", json=payload, timeout=timeout
    ) as r:
        r.raise_for_status()
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
    for event, data in _parse_sse(buf):
        res.events.append(event)
        try:
            obj = json.loads(data) if data else {}
        except json.JSONDecodeError:
            obj = {}
        if event == "session":
            res.run_id = obj.get("run_id")
        elif event == "routing_decision":
            res.routing = obj.get("decision") or obj
            res.intent = (res.routing or {}).get("intent")
        elif event == "token":
            tokens.append(obj.get("text", ""))
        elif event == "final":
            res.final = obj.get("content", "") or "".join(tokens)
        elif event == "deck":
            res.deck = obj
        elif event == "search_results":
            res.search_results = obj.get("candidates", [])
        elif event == "error":
            res.error = obj.get("message", "")
    if not res.final and tokens:
        res.final = "".join(tokens)
    return res


if __name__ == "__main__":
    sid = create_session()
    print("session:", sid)
    info = add_paper(sid, 52)  # Attention Is All You Need
    print("added:", info["title"], "cache_hit=", info.get("cache_hit"))
    out = chat(sid, "What is the purpose of multi-head attention in this paper?")
    print("run_id:", out.run_id, "intent:", out.intent)
    print("final[:600]:", out.final[:600])
