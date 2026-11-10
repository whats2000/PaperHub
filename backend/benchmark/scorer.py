"""Pull grounding evidence + deterministic checks for a finished run.

Scoring is "answer correctness + grounding": a reviewer reads the answer and
the *actual cited chunk text* and judges 0/1. This module gathers everything
that judgement needs from SQLite (no re-run): the trace step DAG, the chunk
IDs each subagent read/cited, the cited chunk text, and a set of cheap
deterministic signals (intent match, all-steps-ok, citations resolve to real
chunks, deck present for slides) that flag obvious failures up front.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paperhub.agents.core.citations import cited_figures, cited_views
from paperhub.pipelines.page_pdf import page_pdf_path
from paperhub.pipelines.paper_asset import read_paper_asset

_CHUNK_RE = re.compile(r"\[chunk:\s*([0-9,\s]+)\]")


@dataclass
class StepInfo:
    step_index: int
    tool: str
    status: str
    latency_ms: int | None
    error: str | None
    result: dict[str, Any] | None


@dataclass
class CitedChunk:
    id: int
    paper_content_id: int | None
    section: str | None
    page: int | None
    text: str


@dataclass
class CitedFigure:
    """A ``[fig:P/ID]`` the answer cites: the judge sees its image."""

    key: str
    paper_content_id: int
    figure_id: str
    caption: str
    image_path: str | None  # absolute; None when the figure or file is gone


@dataclass
class CitedView:
    """A ``[view:P/N/box]`` the answer cites: the judge sees that region."""

    key: str
    paper_content_id: int
    page: int                 # 1-based
    box: list[int]            # 0-1000 page coordinates
    pdf_path: str | None      # the page PDF; None when the paper has none


@dataclass
class Grounding:
    run_id: int
    steps: list[StepInfo] = field(default_factory=list)
    cited_chunk_ids: list[int] = field(default_factory=list)
    cited_chunks: list[CitedChunk] = field(default_factory=list)
    cited_figures: list[CitedFigure] = field(default_factory=list)
    cited_views: list[CitedView] = field(default_factory=list)
    auto_checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _load_steps(conn: sqlite3.Connection, run_id: int) -> list[StepInfo]:
    rows = conn.execute(
        "SELECT step_index, tool, status, latency_ms, error, result_summary_json "
        "FROM tool_calls WHERE run_id = ? ORDER BY step_index",
        (run_id,),
    ).fetchall()
    steps: list[StepInfo] = []
    for si, tool, status, latency, error, result_json in rows:
        result: dict[str, Any] | None
        try:
            result = json.loads(result_json) if result_json else None
        except (json.JSONDecodeError, TypeError):
            result = None
        steps.append(
            StepInfo(int(si), str(tool), str(status), latency, error, result)
        )
    return steps


def parse_citation_ids(text: str) -> list[int]:
    """Extract chunk IDs from ``[chunk:N]`` / ``[chunk:a, b]`` markers in text."""
    out: list[int] = []
    for m in _CHUNK_RE.finditer(text or ""):
        for part in m.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
    # de-dup, preserve order
    seen: set[int] = set()
    uniq: list[int] = []
    for cid in out:
        if cid not in seen:
            seen.add(cid)
            uniq.append(cid)
    return uniq


def _read_chunks(conn: sqlite3.Connection, ids: list[int]) -> list[CitedChunk]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, paper_content_id, section, page, text "
        f"FROM chunks WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {
        int(r[0]): CitedChunk(int(r[0]), r[1], r[2], r[3], str(r[4])) for r in rows
    }
    return [by_id[c] for c in ids if c in by_id]


def _read_views(conn: sqlite3.Connection, keys: list[str]) -> list[CitedView]:
    out: list[CitedView] = []
    for key in keys:
        pid_s, page_s, box_s = key.split("/", 2)
        row = conn.execute(
            "SELECT source_dir_path FROM paper_content WHERE id = ?", (int(pid_s),)).fetchone()
        pdf = page_pdf_path(Path(row[0])) if row and row[0] else None
        out.append(CitedView(key=key, paper_content_id=int(pid_s), page=int(page_s),
                             box=[int(v) for v in box_s.split(",")],
                             pdf_path=str(pdf) if pdf else None))
    return out


def _read_figures(conn: sqlite3.Connection, keys: list[str]) -> list[CitedFigure]:
    out: list[CitedFigure] = []
    for key in keys:
        pid_s, fid = key.split("/", 1)
        pid = int(pid_s)
        row = conn.execute(
            "SELECT source_dir_path FROM paper_content WHERE id = ?", (pid,)).fetchone()
        src = Path(row[0]) if row and row[0] else None
        asset = read_paper_asset(src) if src is not None else None
        fig = next((f for f in (asset.figures if asset else []) if f.id == fid), None)
        path = fig.abs_image_path(src) if fig is not None and src is not None else None
        out.append(CitedFigure(
            key=key, paper_content_id=pid, figure_id=fid,
            caption=fig.caption if fig is not None else "",
            image_path=str(path) if path is not None and path.is_file() else None,
        ))
    return out


def grounding_for(
    db_path: str | Path,
    run_id: int,
    *,
    final_answer: str,
    expect_intent: str | None,
    actual_intent: str | None,
    deck: dict[str, Any] | None,
    search_results: list[dict[str, Any]] | None = None,
) -> Grounding:
    conn = sqlite3.connect(str(db_path))
    try:
        steps = _load_steps(conn, run_id)
        g = Grounding(run_id=run_id, steps=steps)

        # Cited chunk IDs: prefer the markers in the final answer (what the user
        # actually saw cited); union with subagent-recorded chunks_cited_ids.
        cited = parse_citation_ids(final_answer)
        for s in steps:
            if s.result and isinstance(s.result.get("chunks_cited_ids"), list):
                for cid in s.result["chunks_cited_ids"]:
                    if isinstance(cid, int) and cid not in cited:
                        cited.append(cid)
        g.cited_chunk_ids = cited
        g.cited_chunks = _read_chunks(conn, cited)
        g.cited_figures = _read_figures(conn, cited_figures(final_answer or ""))
        g.cited_views = _read_views(conn, cited_views(final_answer or ""))

        # Deterministic signals, branched by intent ----------------------------
        # The auto-checks only flag *obvious* failures; the 0/1 correctness call
        # is the reviewer's, reading the answer against the evidence below.
        intent = actual_intent or expect_intent
        intent_ok = (expect_intent is None) or (actual_intent == expect_intent)
        steps_ok = bool(steps) and all(s.status == "ok" for s in steps)
        errored = [s for s in steps if s.status == "error"]
        answered = bool((final_answer or "").strip())

        checks: dict[str, bool] = {"intent_match": intent_ok, "all_steps_ok": steps_ok}

        if intent == "slides":
            deck_ok = bool(deck) and int((deck or {}).get("page_count", 0)) > 0
            checks["deck_generated"] = deck_ok
            if not deck_ok:
                g.notes.append("no deck / zero pages")
        elif intent == "paper_qa":
            cites_present = len(cited) > 0
            cites_resolve = bool(g.cited_chunks) and len(g.cited_chunks) == len(cited)
            checks["citations_present"] = cites_present
            checks["citations_resolve"] = cites_resolve
            if not cites_present:
                g.notes.append("answer cites no chunks")
            if cited and not cites_resolve:
                missing = [c for c in cited if c not in {cc.id for cc in g.cited_chunks}]
                g.notes.append(f"cited chunk ids not in DB: {missing}")
        elif intent in ("paper_search", "paper_suggest"):
            n = len(search_results or [])
            checks["results_returned"] = n > 0
            if n == 0:
                g.notes.append("no search-result candidates")
        else:
            # chitchat / library_stats / memory / clarify — a non-empty answer
            # is the only deterministic signal; correctness is for the reviewer.
            checks["answered"] = answered
            if not answered:
                g.notes.append("empty answer")

        g.auto_checks = checks
        if errored:
            g.notes.append(
                "errored steps: " + ", ".join(f"{s.tool}#{s.step_index}" for s in errored)
            )
        return g
    finally:
        conn.close()
