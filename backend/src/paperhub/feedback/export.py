"""Hugging Face SFT / DPO export of answer feedback (Plan H2, FR-19).

``build_export`` turns counted feedback into samples without writing anything
(the preview endpoint calls it alone); ``write_export`` writes them under
``exports_dir/<YYYYmmdd-HHMMSS>/`` as ``sft.jsonl``, ``dpo.jsonl`` and a
``README.md`` dataset card.

* SFT: one line per thumbs-up answer, ``{"messages": [system?, *history,
  user, assistant]}``. The system message holds the cited passages as
  ``<sources>…</sources>``, built from the ``chunks`` rows the answers cite
  (``[chunk:N]`` and multi-id markers, see ``feedback.citations``).
* DPO: ``{"prompt": messages, "chosen": [assistant], "rejected": [assistant]}``
  from (1) a thumbs-down answer with a correction (the correction is chosen)
  and (2) fork siblings: the original run and the fork's first answer, ONLY
  when the two prompts (history + question) are identical after trimming and
  the answers have opposite polarity.

With ``include_private`` off a sample is dropped, failing closed, when ANY
message in it (history, answers, correction) cites a chunk of an
``is_private`` paper or a chunk id that no longer resolves, or its session has
a private paper attached (see ``build_export``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.feedback.citations import cited_chunk_ids
from paperhub.feedback.forks import copied_runs
from paperhub.feedback.models import ExportRequest

Message = dict[str, str]


@dataclass
class _Chunk:
    text: str
    paper_title: str
    is_private: bool


@dataclass
class _Answer:
    """One answered run with the prompt it answered."""

    run_id: int
    session_id: int
    history: list[Message]
    question: str
    answer: str
    model: str | None
    prefix_len: int  # messages (any role) before the question in the session


@dataclass
class _Sample:
    """A candidate sample before filtering."""

    kind: str  # "sft" | "dpo_correction" | "dpo_fork"
    texts: list[str]  # every message text the sample carries (for citations)
    answer_texts: list[str]  # the answers whose citations build <sources>
    models: list[str | None]
    created_at: str


@dataclass
class ExportPlan:
    options: ExportRequest
    sft: list[dict[str, Any]]
    dpo: list[dict[str, Any]]
    includes_private: bool
    dpo_from_corrections: int = 0
    dpo_from_forks: int = 0


async def _answer(conn: aiosqlite.Connection, run_id: int) -> _Answer | None:
    """The run's question/answer and the session history before the question."""
    async with conn.execute(
        "SELECT session_id FROM runs WHERE id = ?", (run_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    async with conn.execute(
        "SELECT role, content, run_id FROM messages WHERE session_id = ? "
        "ORDER BY id", (int(row[0]),),
    ) as cur:
        turns = [(str(r[0]), str(r[1]), r[2]) for r in await cur.fetchall()]
    a_idx = next(
        (i for i in range(len(turns) - 1, -1, -1)
         if turns[i][0] == "assistant" and turns[i][2] == run_id),
        None,
    )
    if a_idx is None:
        return None
    q_idx = next(
        (i for i, t in enumerate(turns) if t[0] == "user" and t[2] == run_id), None,
    )
    if q_idx is None:  # fall back to the user turn right before the answer
        q_idx = next((i for i in range(a_idx - 1, -1, -1) if turns[i][0] == "user"),
                     None)
    if q_idx is None or q_idx > a_idx:
        return None
    history = [
        {"role": r, "content": c} for r, c, _ in turns[:q_idx]
        if r in ("user", "assistant")
    ]
    return _Answer(
        run_id=run_id, session_id=int(row[0]), history=history,
        question=turns[q_idx][1], answer=turns[a_idx][1],
        model=await answering_model(conn, run_id), prefix_len=q_idx,
    )


async def answering_model(conn: aiosqlite.Connection, run_id: int) -> str | None:
    """The model of the run's answering step.

    Heuristic: the LAST model-bearing ``tool_calls`` step that is not the
    router (``paper_qa:finalize``, ``chitchat:generate``,
    ``report:slide_agent`` …). A run with no traced model gives ``None``, so
    any ``model`` filter excludes it."""
    async with conn.execute(
        "SELECT model FROM tool_calls WHERE run_id = ? AND model IS NOT NULL "
        "AND agent != 'router' ORDER BY step_index DESC LIMIT 1", (run_id,),
    ) as cur:
        row = await cur.fetchone()
    return str(row[0]) if row is not None else None


async def _chunks(conn: aiosqlite.Connection, ids: set[int]) -> dict[int, _Chunk]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    async with conn.execute(
        "SELECT c.id, c.text, pc.title, pc.is_private FROM chunks c "
        "JOIN paper_content pc ON pc.id = c.paper_content_id "
        f"WHERE c.id IN ({marks})", tuple(ids),
    ) as cur:
        return {
            int(r[0]): _Chunk(text=str(r[1]), paper_title=str(r[2]),
                              is_private=bool(r[3]))
            for r in await cur.fetchall()
        }


def _sources_message(ids: list[int], chunks: dict[int, _Chunk]) -> Message | None:
    parts = [
        f'<source chunk="{i}" paper="{_attr(chunks[i].paper_title)}">\n'
        f"{chunks[i].text}\n</source>"
        for i in ids if i in chunks
    ]
    if not parts:
        return None
    return {"role": "system",
            "content": "<sources>\n" + "\n".join(parts) + "\n</sources>"}


def _attr(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _same_prompt(a: _Answer, b: _Answer) -> bool:
    """Identical after trimming: the question AND every history turn."""
    if a.question.strip() != b.question.strip() or len(a.history) != len(b.history):
        return False
    return all(
        x["role"] == y["role"] and x["content"].strip() == y["content"].strip()
        for x, y in zip(a.history, b.history, strict=True)
    )


def _prompt(ans: _Answer, sources: Message | None) -> list[Message]:
    return ([sources] if sources else []) + ans.history + [
        {"role": "user", "content": ans.question},
    ]


async def _polarities(
    conn: aiosqlite.Connection, include_implicit: bool,
) -> dict[int, tuple[str, str, str | None]]:
    """run_id -> (polarity, created_at, correction) of counted feedback in live
    sessions. Explicit feedback wins; with ``include_implicit`` a confirmed
    implicit flag marks an otherwise-unrated run ``down``. Fork copies of one
    answer (and the original) collapse to one run (one sample per answer)."""
    out: dict[int, tuple[str, str, str | None]] = {}
    async with conn.execute(
        "SELECT f.run_id, f.kind, f.polarity, f.created_at, f.correction "
        "FROM feedback f JOIN runs r ON r.id = f.run_id "
        "JOIN chat_sessions s ON s.id = r.session_id "
        "WHERE s.deleted_at IS NULL AND (f.kind = 'explicit' OR "
        "(f.kind = 'implicit' AND f.status = 'final' AND f.verdict = 'confirmed')) "
        "ORDER BY f.kind, f.id",  # 'explicit' sorts first
    ) as cur:
        rows = await cur.fetchall()
    for run_id, kind, polarity, created_at, correction in rows:
        rid = int(run_id)
        if kind == "implicit" and (not include_implicit or rid in out):
            continue
        out[rid] = (str(polarity), str(created_at), correction)
    # A fork's copied turn is the same answer as its original: keep ONE run
    # per origin (the original when rated, else the earliest rated copy), so a
    # fork-of-fork copy and the intermediate fork's copy count once even when
    # the original is unrated or its session is deleted.
    copies = await copied_runs(conn)
    keep: dict[int, int] = {}
    for rid in sorted(out):
        origin = copies.get(rid, rid)
        if rid == origin or origin not in keep:
            keep[origin] = rid
    kept = set(keep.values())
    return {rid: v for rid, v in out.items() if rid in kept}


async def _fork_pairs(
    conn: aiosqlite.Connection,
) -> list[tuple[int, int]]:
    """(original run, fork's first run) for every live fork."""
    async with conn.execute(
        "SELECT s.id, s.forked_from_run_id FROM chat_sessions s "
        "JOIN runs r ON r.id = s.forked_from_run_id "
        "JOIN chat_sessions p ON p.id = r.session_id "
        "WHERE s.forked_from_run_id IS NOT NULL AND s.deleted_at IS NULL "
        "AND p.deleted_at IS NULL ORDER BY s.id",
    ) as cur:
        forks = [(int(r[0]), int(r[1])) for r in await cur.fetchall()]
    pairs: list[tuple[int, int]] = []
    for fork_sid, orig_run in forks:
        # The fork copies every message before the forked question, each copied
        # turn on a fresh run; its first NEW run is the first run whose user
        # message comes after that copied prefix.
        async with conn.execute(
            "SELECT COUNT(*) FROM messages m WHERE m.session_id = "
            "(SELECT session_id FROM runs WHERE id = ?) AND m.id < "
            "(SELECT MIN(id) FROM messages WHERE run_id = ? AND role = 'user')",
            (orig_run, orig_run),
        ) as cur:
            prefix = int((await cur.fetchone() or (0,))[0])
        async with conn.execute(
            "SELECT role, run_id FROM messages WHERE session_id = ? ORDER BY id",
            (fork_sid,),
        ) as cur:
            fturns = [(str(r[0]), r[1]) for r in await cur.fetchall()][prefix:]
        first = next(
            (int(run) for role, run in fturns if role == "user" and run is not None),
            None,
        )
        if first is not None:
            pairs.append((orig_run, first))
    return pairs


async def _down_runs(conn: aiosqlite.Connection) -> set[int]:
    """Runs rated down: explicit ``down`` or a confirmed implicit flag (always,
    whatever ``include_implicit`` says: this guards what we train on)."""
    async with conn.execute(
        "SELECT run_id FROM feedback WHERE polarity = 'down' AND (kind = 'explicit' "
        "OR (kind = 'implicit' AND status = 'final' AND verdict = 'confirmed'))",
    ) as cur:
        return {int(r[0]) for r in await cur.fetchall()}


async def _history_has_down(
    conn: aiosqlite.Connection, session_id: int, prefix_len: int, down: set[int],
) -> bool:
    """Whether an assistant turn among the session's first ``prefix_len``
    messages was rated down. A fork's copied turns sit on fresh runs, so the
    parent session's matching prefix is checked too (recursively)."""
    async with conn.execute(
        "SELECT role, run_id FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ) as cur:
        turns = [(str(r[0]), r[1]) for r in await cur.fetchall()][:prefix_len]
    if any(role == "assistant" and run is not None and int(run) in down
           for role, run in turns):
        return True
    async with conn.execute(
        "SELECT r.session_id, (SELECT COUNT(*) FROM messages m "
        " WHERE m.session_id = r.session_id AND m.id < "
        " (SELECT MIN(id) FROM messages WHERE run_id = r.id AND role = 'user')) "
        "FROM chat_sessions s JOIN runs r ON r.id = s.forked_from_run_id "
        "WHERE s.id = ?", (session_id,),
    ) as cur:
        parent = await cur.fetchone()
    if parent is None or int(parent[0]) == session_id:
        return False
    return await _history_has_down(
        conn, int(parent[0]), min(prefix_len, int(parent[1])), down,
    )


async def _session_has_private(conn: aiosqlite.Connection, session_id: int) -> bool:
    """Any private paper attached to the session (enabled or not)."""
    async with conn.execute(
        "SELECT 1 FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND pc.is_private = 1 LIMIT 1", (session_id,),
    ) as cur:
        return await cur.fetchone() is not None


async def build_export(conn: aiosqlite.Connection, opts: ExportRequest) -> ExportPlan:
    """Collect, filter and format every exportable sample (writes nothing).

    Private documents fail CLOSED. A sample is "possibly private" when it cites
    a chunk of an ``is_private`` paper anywhere, cites a chunk id that no
    longer resolves (re-ingest rebuilds chunks, so its source is unknown), or
    its session has a private paper attached. ``include_private=false`` drops
    such samples; otherwise they set ``includes_private``.

    An SFT sample is dropped when its history holds an assistant turn rated
    down, so no down-rated answer becomes a training target."""
    pol = await _polarities(conn, opts.include_implicit)
    down = await _down_runs(conn)
    private_sessions: dict[int, bool] = {}

    async def session_private(sid: int) -> bool:
        if sid not in private_sessions:
            private_sessions[sid] = await _session_has_private(conn, sid)
        return private_sessions[sid]
    answers: dict[int, _Answer | None] = {}

    async def ans(run_id: int) -> _Answer | None:
        if run_id not in answers:
            answers[run_id] = await _answer(conn, run_id)
        return answers[run_id]

    candidates: list[tuple[_Sample, list[_Answer], str | None]] = []
    for run_id, (polarity, created_at, correction) in pol.items():
        a = await ans(run_id)
        if a is None:
            continue
        if polarity == "up":
            if await _history_has_down(conn, a.session_id, a.prefix_len, down):
                continue  # a down-rated answer would be trained on via history
            candidates.append((_Sample(
                kind="sft", texts=[*(m["content"] for m in a.history), a.question,
                                   a.answer],
                answer_texts=[a.answer], models=[a.model], created_at=created_at,
            ), [a], None))
        elif correction and correction.strip():
            candidates.append((_Sample(
                kind="dpo_correction",
                texts=[*(m["content"] for m in a.history), a.question, a.answer,
                       correction],
                answer_texts=[a.answer, correction], models=[a.model],
                created_at=created_at,
            ), [a], correction))

    for orig_run, fork_run in await _fork_pairs(conn):
        p1, p2 = pol.get(orig_run), pol.get(fork_run)
        if p1 is None or p2 is None or p1[0] == p2[0]:
            continue
        a1, a2 = await ans(orig_run), await ans(fork_run)
        if a1 is None or a2 is None or not _same_prompt(a1, a2):
            continue  # an edited fork is a different prompt: no pair
        chosen, rejected = (a1, a2) if p1[0] == "up" else (a2, a1)
        candidates.append((_Sample(
            kind="dpo_fork",
            texts=[*(m["content"] for m in a1.history), a1.question, a1.answer,
                   a2.answer],
            answer_texts=[chosen.answer, rejected.answer],
            models=[a1.model, a2.model], created_at=max(p1[1], p2[1]),
        ), [chosen, rejected, a1], None))

    all_ids = {i for s, _, _ in candidates for t in s.texts for i in cited_chunk_ids(t)}
    chunks = await _chunks(conn, all_ids)

    plan = ExportPlan(options=opts, sft=[], dpo=[], includes_private=False)
    for sample, pair, correction in sorted(candidates, key=lambda c: c[0].created_at):
        if not _passes(sample, opts):
            continue
        cited = [i for t in sample.answer_texts for i in cited_chunk_ids(t)]
        cited = [i for i in dict.fromkeys(cited) if i in chunks]
        if opts.cited_only and not cited:
            continue
        all_cited = [i for t in sample.texts for i in cited_chunk_ids(t)]
        private = (
            any(i not in chunks or chunks[i].is_private for i in all_cited)
            or any([await session_private(a.session_id) for a in pair])
        )
        if private and not opts.include_private:
            continue
        plan.includes_private = plan.includes_private or private
        sources = _sources_message(cited, chunks)
        first = pair[0]
        if sample.kind == "sft":
            plan.sft.append({"messages": [
                *_prompt(first, sources), {"role": "assistant", "content": first.answer},
            ]})
        elif sample.kind == "dpo_correction":
            assert correction is not None
            plan.dpo.append({
                "prompt": _prompt(first, sources),
                "chosen": [{"role": "assistant", "content": correction}],
                "rejected": [{"role": "assistant", "content": first.answer}],
            })
            plan.dpo_from_corrections += 1
        else:
            plan.dpo.append({  # the ORIGINAL run's prompt text
                "prompt": _prompt(pair[2], sources),
                "chosen": [{"role": "assistant", "content": pair[0].answer}],
                "rejected": [{"role": "assistant", "content": pair[1].answer}],
            })
            plan.dpo_from_forks += 1
    return plan


def utc_day(value: str) -> str:
    """Normalise a ``since``/``until`` value to a ``YYYY-MM-DD`` UTC day.

    Accepts an ISO date or datetime; an aware datetime is converted to UTC, a
    naive one is taken as UTC (feedback ``created_at`` is SQLite UTC). Raises
    ``ValueError`` on anything else."""
    v = value.strip()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        dt = datetime.fromisoformat(v)  # raises ValueError when not ISO
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.date().isoformat()


def _passes(sample: _Sample, opts: ExportRequest) -> bool:
    """Date bounds are inclusive UTC days of the feedback ``created_at``."""
    day = sample.created_at[:10]
    if opts.since and day < utc_day(opts.since):
        return False
    if opts.until and day > utc_day(opts.until):
        return False
    return not (opts.model and any(m != opts.model for m in sample.models))


def _dataset_card(plan: ExportPlan, created: str) -> str:
    o = plan.options
    lines = [
        "---",
        "configs:",
        "- config_name: sft",
        "  data_files: sft.jsonl",
        "- config_name: dpo",
        "  data_files: dpo.jsonl",
        "---",
        "",
        "# PaperHub feedback dataset",
        "",
        f"Exported {created} from PaperHub answer feedback.",
        "",
        "> **Warning: contains document text.** The `<sources>` system messages "
        "quote passages of the papers the answers cite, and the answers "
        "paraphrase them. Check the papers' licences before sharing.",
        "",
    ]
    if plan.includes_private:
        lines += [
            "> **Includes private documents.** At least one sample cites a "
            "paper marked private, cites a passage that no longer resolves, or "
            "comes from a chat with a private paper attached. Do not publish "
            "this dataset.",
            "",
        ]
    if not o.include_private:
        lines += [
            "> **Private documents excluded (fail-closed).** Every sample that "
            "cites a paper marked private or a passage that no longer resolves "
            "is dropped, and so is every sample from a chat session with a "
            "private paper attached, even when the sample cites none of it.",
            "",
        ]
    lines += [
        "## Counts",
        "",
        "| File | Samples |",
        "| --- | --- |",
        f"| `sft.jsonl` | {len(plan.sft)} |",
        f"| `dpo.jsonl` | {len(plan.dpo)} "
        f"({plan.dpo_from_corrections} from corrections, "
        f"{plan.dpo_from_forks} from fork siblings) |",
        "",
        "## Options",
        "",
        f"- include_implicit: {str(o.include_implicit).lower()}",
        f"- include_private: {str(o.include_private).lower()}",
        f"- since (UTC day, inclusive): {utc_day(o.since) if o.since else 'any'}",
        f"- until (UTC day, inclusive): {utc_day(o.until) if o.until else 'any'}",
        f"- model: {o.model or 'any'}",
        f"- cited_only: {str(o.cited_only).lower()}",
        "",
        "## Format",
        "",
        '- `sft.jsonl`: `{"messages": [system?, ...history, user, assistant]}` '
        "per thumbs-up answer. Only the FINAL assistant turn is a vetted "
        "target; history turns are context (mask them from the loss). Samples "
        "whose history holds a down-rated answer are left out.",
        '- `dpo.jsonl`: `{"prompt": [...], "chosen": [assistant], '
        '"rejected": [assistant]}`.',
        "",
    ]
    return "\n".join(lines)


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def write_export(
    plan: ExportPlan, exports_dir: Path, now: datetime | None = None,
) -> Path:
    """Write ``sft.jsonl``, ``dpo.jsonl`` and ``README.md`` into a new
    ``exports_dir/<YYYYmmdd-HHMMSS>[-N]/`` (UTC) and return that directory.
    Bytes, not text mode, so the JSONL stays LF-only on Windows. Blocking:
    call it off the event loop."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    exports_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:  # claim a fresh dir atomically (two exports in one second)
        out = exports_dir / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            out.mkdir()
            break
        except FileExistsError:
            n += 1
    (out / "sft.jsonl").write_bytes(_jsonl(plan.sft).encode("utf-8"))
    (out / "dpo.jsonl").write_bytes(_jsonl(plan.dpo).encode("utf-8"))
    (out / "README.md").write_bytes(_dataset_card(plan, stamp).encode("utf-8"))
    return out


EXPORT_FILES = ["sft.jsonl", "dpo.jsonl", "README.md"]
