"""Fork-copied runs (Plan H2).

``fork_session`` copies every message before the forked one onto FRESH runs
in the fork session. Such a copied run is the same answer as its original, so
feedback must not review or count it twice. ``copied_runs`` maps each copied
run to the run it was copied from, by position: the fork's first ``prefix``
messages are the source session's first ``prefix`` messages, where ``prefix``
is the number of source messages before the forked user message.
"""
from __future__ import annotations

import aiosqlite


async def _session_runs(
    conn: aiosqlite.Connection, session_id: int,
) -> list[int | None]:
    async with conn.execute(
        "SELECT run_id FROM messages WHERE session_id = ? ORDER BY id", (session_id,),
    ) as cur:
        return [None if r[0] is None else int(r[0]) for r in await cur.fetchall()]


async def _fork_copies(
    conn: aiosqlite.Connection, fork_sid: int, forked_run: int,
) -> dict[int, int]:
    """Copied run -> source run for one fork session (one level)."""
    async with conn.execute(
        "SELECT session_id FROM runs WHERE id = ?", (forked_run,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return {}
    async with conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND id < "
        "(SELECT MIN(id) FROM messages WHERE run_id = ? AND role = 'user')",
        (int(row[0]), forked_run),
    ) as cur:
        prefix = int((await cur.fetchone() or (0,))[0])
    source = (await _session_runs(conn, int(row[0])))[:prefix]
    copied = (await _session_runs(conn, fork_sid))[:prefix]
    return {
        c: s for c, s in zip(copied, source, strict=False)
        if c is not None and s is not None and c != s
    }


async def copied_in_session(conn: aiosqlite.Connection, session_id: int) -> set[int]:
    """The runs of ``session_id`` copied by a fork (empty for a non-fork)."""
    async with conn.execute(
        "SELECT forked_from_run_id FROM chat_sessions WHERE id = ? "
        "AND forked_from_run_id IS NOT NULL", (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return set()
    return set(await _fork_copies(conn, session_id, int(row[0])))


async def copied_runs(conn: aiosqlite.Connection) -> dict[int, int]:
    """Copied run -> ORIGINAL run (followed through forks of forks) for every
    fork session, soft-deleted ones included (the copy relation is
    structural; callers filter deleted sessions themselves)."""
    async with conn.execute(
        "SELECT id, forked_from_run_id FROM chat_sessions "
        "WHERE forked_from_run_id IS NOT NULL",
    ) as cur:
        forks = [(int(r[0]), int(r[1])) for r in await cur.fetchall()]
    out: dict[int, int] = {}
    for fork_sid, forked_run in forks:
        out.update(await _fork_copies(conn, fork_sid, forked_run))
    for run in list(out):
        origin, seen = out[run], {run}
        while origin in out and origin not in seen:
            seen.add(origin)
            origin = out[origin]
        out[run] = origin
    return out
