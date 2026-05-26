"""Benchmark config loader (TOML).

A config defines reusable *paper sets* (lists of source keys that already live
in the user's cache, e.g. ``arxiv:1706.03762`` or ``sha256:<hash>``) and a list
of *cases* — each a simulated user prompt routed at the live backend, plus the
expected intent and a free-text rubric for the (human/LLM) reviewer.

See ``cases.example.toml`` for the schema in practice.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Case:
    id: str
    prompt: str
    papers: list[str]              # resolved source keys (cache content_keys)
    expect_intent: str | None = None
    rubric: str = ""
    session_group: str | None = None   # cases sharing a group run as one chat
    current_view_page: int = 0


@dataclass
class BenchmarkConfig:
    name: str
    base_url: str
    db_path: str
    cases: list[Case] = field(default_factory=list)


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
        cases.append(
            Case(
                id=cid,
                prompt=str(c["prompt"]),
                papers=papers,
                expect_intent=(str(c["expect_intent"]) if c.get("expect_intent") else None),
                rubric=str(c.get("rubric", "")),
                session_group=(str(c["session_group"]) if c.get("session_group") else None),
                current_view_page=int(c.get("current_view_page", 0)),
            )
        )

    if not cases:
        raise ValueError(f"{p}: no [[cases]] defined")
    return BenchmarkConfig(name=name, base_url=base_url, db_path=db_path, cases=cases)
