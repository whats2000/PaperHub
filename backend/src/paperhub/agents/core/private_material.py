"""Collect the turn's private material for the outbound checkpoint (H5a).

Provenance (SRS H5a stage 4): private documents attached to the chat, the
chunks read from them this turn, private rows returned by ``query_library``,
and the idea's problem text in an idea chat. The user's own typed message is never added.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.outbound.context import PrivateItem, add_private, mark_incomplete
from paperhub.outbound.spans import contains_title, guarded_title

DIGEST_CHARS = 1_500


def _digest_summary(source_dir: object) -> str:
    """The cached digest's abstract (no new LLM work); empty when absent."""
    if not source_dir:
        return ""
    try:
        data = json.loads(
            (Path(str(source_dir)) / "digest.json").read_text(encoding="utf-8"))
        return str(data.get("abstract") or "")[:DIGEST_CHARS]
    except (OSError, ValueError, AttributeError):
        return ""


async def collect_turn_start(conn: aiosqlite.Connection, session_id: int) -> None:
    """Add the enabled private documents and the idea's problem text."""
    async with conn.execute(
        "SELECT pc.id, pc.title, pc.source_dir_path FROM papers p "
        "JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 AND pc.is_private = 1 "
        "ORDER BY p.added_at, p.id",
        (session_id,),
    ) as cur:
        docs = await cur.fetchall()
    for pid, title, source_dir in docs:
        add_private(PrivateItem("document", f"doc:{pid}", str(title or ""),
                                _digest_summary(source_dir)))
    async with conn.execute(
        "SELECT p.id, p.text FROM idea_sessions s "
        "JOIN ideas i ON i.id = s.idea_id JOIN problems p ON p.id = i.problem_id "
        "WHERE s.session_id = ? ORDER BY i.id DESC LIMIT 1",
        (session_id,),
    ) as cur:
        problem = await cur.fetchone()
    if problem is not None and problem[1]:
        add_private(PrivateItem("problem", f"problem:{problem[0]}", "",
                                str(problem[1])))


async def collect_chunks(conn: aiosqlite.Connection, chunk_ids: list[int]) -> None:
    """Add those of ``chunk_ids`` that belong to a private paper (one query)."""
    ids = list(dict.fromkeys(chunk_ids))
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    async with conn.execute(
        "SELECT c.id, pc.title, c.text FROM chunks c "  # noqa: S608
        "JOIN paper_content pc ON pc.id = c.paper_content_id "
        f"WHERE pc.is_private = 1 AND c.id IN ({marks})",
        tuple(ids),
    ) as cur:
        rows = await cur.fetchall()
    for cid, title, text in rows:
        add_private(PrivateItem("chunk", f"chunk:{cid}", str(title or ""),
                                str(text or "")))


# query_library (SQL over the whole library) can return any column of a
# private document. Cells shorter than this carry no passage worth guarding.
_CELL_MIN = 12
# A cell this long that no single paper field or chunk contains (a substr or
# a concatenation) could still be private text: the turn becomes unsure.
_UNATTRIBUTED_MIN = 80
# Database lookups per query_library call; beyond it the turn becomes unsure.
_LOOKUP_CAP = 100
_ID_COLUMNS = frozenset({"paper_content_id", "pcid"})


def _cell_norm(text: str) -> str:
    return " ".join(text.casefold().split())


async def _owners(conn: aiosqlite.Connection, text: str, *, long: bool) -> set[tuple[int, bool]]:
    """``(paper_content_id, is_private)`` of the papers whose title, abstract,
    authors or sections, or (long cells only for public papers) chunks
    contain ``text`` verbatim."""
    async with conn.execute(
        "SELECT id, is_private FROM paper_content WHERE instr(title, ?) > 0 "
        "OR instr(abstract, ?) > 0 OR instr(authors_json, ?) > 0 "
        "OR instr(COALESCE(sections_json, ''), ?) > 0 LIMIT 20",
        (text, text, text, text),
    ) as cur:
        found = {(int(r[0]), bool(r[1])) for r in await cur.fetchall()}
    if found:
        return found
    private_only = "" if long else "AND pc.is_private = 1 "
    async with conn.execute(
        "SELECT DISTINCT pc.id, pc.is_private FROM chunks c "  # noqa: S608
        "JOIN paper_content pc ON pc.id = c.paper_content_id "
        f"WHERE instr(c.text, ?) > 0 {private_only}LIMIT 20",
        (text,),
    ) as cur:
        return {(int(r[0]), bool(r[1])) for r in await cur.fetchall()}


async def collect_library_rows(
    conn: aiosqlite.Connection,
    candidate_ids: list[int],
    queries: list[tuple[list[str], list[list[Any]]]],
) -> None:
    """Register the private material a ``query_library`` call returned.

    ``candidate_ids``: the papers it picked as cards; ``queries``: each SQL
    query's ``(columns, rows)``. A row is attributed to a private document by
    a ``paper_content_id`` column, a ``chunk_id`` column, the document's
    title, or a cell that the document's fields or chunks contain verbatim;
    the row's text is then added under that title. A long cell that cannot
    be attributed (or a call past the lookup cap) marks the turn's material
    incomplete, so later outbound requests follow the unsure policy."""
    async with conn.execute(
        "SELECT id, title, source_dir_path, is_private FROM paper_content"
    ) as cur:
        papers = await cur.fetchall()
    private = {int(r[0]): (str(r[1] or ""), r[2]) for r in papers if r[3]}
    if not private:
        return  # nothing private in the library: nothing to attribute
    for pcid in dict.fromkeys(candidate_ids):
        if pcid in private:
            title, source_dir = private[pcid]
            add_private(PrivateItem("document", f"doc:{pcid}", title,
                                    _digest_summary(source_dir)))
    public_titles = {_cell_norm(str(r[1] or "")) for r in papers if not r[3]}
    private_titles = {pcid: (_cell_norm(t), guarded_title(t)) for pcid, (t, _) in private.items()}
    lookups = 0
    for columns, rows in queries:
        names = [str(c).lower().rsplit(".", 1)[-1] for c in columns]
        for row in rows:
            hit: set[int] = set()
            chunk_ids: list[int] = []
            texts = [v for v in row if isinstance(v, str) and v.strip()]
            for name, v in zip(names, row, strict=False):
                if isinstance(v, int) and not isinstance(v, bool):
                    if name in _ID_COLUMNS and v in private:
                        hit.add(v)
                    elif name == "chunk_id":
                        chunk_ids.append(v)
            for t in texts:
                norm = _cell_norm(t)
                for pcid, (exact, guarded) in private_titles.items():
                    if norm == exact or (guarded and contains_title(t, guarded)):
                        hit.add(pcid)
            if not hit:
                for t in texts:
                    if len(t) < _CELL_MIN or _cell_norm(t) in public_titles:
                        continue
                    if lookups >= _LOOKUP_CAP:
                        mark_incomplete("query_library returned too many rows to attribute")
                        return
                    lookups += 1
                    owners = await _owners(conn, t, long=len(t) >= _UNATTRIBUTED_MIN)
                    hit |= {pcid for pcid, is_private in owners if is_private}
                    if not owners and len(t) >= _UNATTRIBUTED_MIN:
                        mark_incomplete(
                            "query_library returned text that could not be attributed")
            if chunk_ids:
                await collect_chunks(conn, chunk_ids)
            body = " | ".join(texts)
            for pcid in hit:
                ref = f"sql:{pcid}:{hashlib.sha256(body.encode()).hexdigest()[:12]}"
                add_private(PrivateItem("document", ref, private[pcid][0], body))
