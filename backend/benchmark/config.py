"""Benchmark config loader (TOML).

A config defines reusable *paper sets* (lists of source keys that already live
in the user's cache, e.g. ``arxiv:1706.03762`` or ``sha256:<hash>``) and a list
of *cases* — each a simulated user prompt routed at the live backend, plus the
expected intent and a free-text rubric for the (human/LLM) reviewer.

A case may seed a conversation with ``turns = [{user = "...", assistant =
"..."}]`` (sent as ``history`` in ``POST /chat``), name the H4 ``scenario``
(1-8) it belongs to, and carry a ``kind`` (``chitchat`` / ``library_count``
mark the simple turns the latency gate times). ``[benchmark] suite`` labels
the whole config (``regression`` = the no-regression gate's case set).

See ``cases.example.toml`` for the schema in practice.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCENARIOS = range(1, 9)


@dataclass(frozen=True)
class Turn:
    """One seeded earlier exchange of the conversation."""

    user: str
    assistant: str


@dataclass
class Case:
    id: str
    prompt: str
    papers: list[str]              # resolved source keys (cache content_keys)
    expect_intent: str | None = None
    rubric: str = ""
    session_group: str | None = None   # cases sharing a group run as one chat
    current_view_page: int = 0
    # Slide-aware QA (v2.29): reuse an EXISTING session (with its already-built
    # deck) instead of creating a fresh one + attaching papers, and send the
    # deterministic chip flag so build_slide_context fires.
    session_id: int | None = None
    slide_attached: bool = False
    # H4 scenario benchmark: seeded earlier turns, scenario number (1-8) and a
    # free-form kind tag (e.g. "chitchat", "library_count").
    turns: list[Turn] = field(default_factory=list)
    scenario: int | None = None
    kind: str | None = None


@dataclass
class BenchmarkConfig:
    name: str
    base_url: str
    db_path: str
    cases: list[Case] = field(default_factory=list)
    suite: str | None = None


def history_for(case: Case) -> list[dict[str, str]]:
    """The case's seeded turns as ``POST /chat`` ``history`` entries."""
    out: list[dict[str, str]] = []
    for t in case.turns:
        out.append({"role": "user", "content": t.user})
        out.append({"role": "assistant", "content": t.assistant})
    return out


def _parse_turns(cid: str, raw: Any) -> list[Turn]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"case {cid}: 'turns' must be a list of {{user, assistant}} tables")
    turns: list[Turn] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ValueError(f"case {cid}: turns[{i}] must be a table")
        user, assistant = t.get("user"), t.get("assistant")
        user_ok = isinstance(user, str) and bool(user.strip())
        assistant_ok = isinstance(assistant, str) and bool(assistant.strip())
        if not (user_ok and assistant_ok):
            raise ValueError(
                f"case {cid}: turns[{i}] needs non-empty 'user' and 'assistant' strings")
        turns.append(Turn(user=str(user), assistant=str(assistant)))
    return turns


def _parse_scenario(cid: str, raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw not in SCENARIOS:
        raise ValueError(f"case {cid}: 'scenario' must be an integer 1-8, got {raw!r}")
    return int(raw)


def load_config(path: str | Path) -> BenchmarkConfig:
    p = Path(path)
    with p.open("rb") as fh:
        raw = tomllib.load(fh)

    bench = raw.get("benchmark", {})
    name = str(bench.get("name", p.stem))
    base_url = str(bench.get("base_url", "http://127.0.0.1:8000"))
    db_path = str(bench.get("db_path", "workspace/paperhub.db"))

    papersets: dict[str, list[str]] = {
        k: [str(s) for s in v] for k, v in raw.get("papersets", {}).items()
    }

    cases: list[Case] = []
    for i, c in enumerate(raw.get("cases", [])):
        cid = str(c.get("id") or f"case-{i + 1:02d}")
        # papers: either a named paperset, or an inline list, or none.
        papers: list[str] = []
        if "paperset" in c:
            setname = str(c["paperset"])
            if setname not in papersets:
                raise ValueError(f"case {cid}: unknown paperset '{setname}'")
            papers = list(papersets[setname])
        if "papers" in c:
            papers = papers + [str(s) for s in c["papers"]]
        if not c.get("prompt"):
            raise ValueError(f"case {cid}: missing 'prompt'")
        if c.get("turns") and (c.get("session_group") or c.get("session_id") is not None):
            # A non-empty `history` replaces the session's stored messages on
            # both backends, so seeded turns would silently hide the earlier
            # turns of a shared session.
            raise ValueError(
                f"case {cid}: 'turns' cannot be combined with 'session_group' or "
                "'session_id' (seeded turns replace the session's own history)")
        cases.append(
            Case(
                id=cid,
                prompt=str(c["prompt"]),
                papers=papers,
                expect_intent=(str(c["expect_intent"]) if c.get("expect_intent") else None),
                rubric=str(c.get("rubric", "")),
                session_group=(str(c["session_group"]) if c.get("session_group") else None),
                current_view_page=int(c.get("current_view_page", 0)),
                session_id=(int(c["session_id"]) if c.get("session_id") is not None else None),
                slide_attached=bool(c.get("slide_attached", False)),
                turns=_parse_turns(cid, c.get("turns")),
                scenario=_parse_scenario(cid, c.get("scenario")),
                kind=(str(c["kind"]) if c.get("kind") else None),
            )
        )

    if not cases:
        raise ValueError(f"{p}: no [[cases]] defined")
    suite = str(bench["suite"]) if bench.get("suite") else None
    return BenchmarkConfig(
        name=name, base_url=base_url, db_path=db_path, cases=cases, suite=suite,
    )
