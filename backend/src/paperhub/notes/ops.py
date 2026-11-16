"""The note op engine: every change to a note's blocks goes through here.

One ``apply_ops`` call is one transaction, one revision and one change set.
Rules:

* an op on a block changed after the caller's ``base_revision`` (by anyone,
  including another view of the same user) is rejected ``changed_since_base``
  with the current block; a client advances its base from each result;
* the agent never overwrites or deletes the user's words: its ``update`` /
  ``delete`` on a user-authored block becomes a ``note_suggestions`` row;
* a user edit to an AI block makes it the user's;
* deletes are soft and take the block's subtree with them;
* every applied change writes a ``note_block_history`` row.
"""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel

from paperhub.db.connection import write_transaction
from paperhub.notes.models import Block, BlockType
from paperhub.notes.order import key_between
from paperhub.notes.store import block_text, bump_revision, get_note, new_block_id

Actor = Literal["user", "agent", "import", "system"]
RejectReason = Literal["changed_since_base", "missing", "invalid"]


class BlockIn(BaseModel):
    type: BlockType
    props: dict[str, Any] = {}
    content: list[dict[str, Any]] | str | None = None


class Op(BaseModel):
    op: Literal["insert", "update", "delete", "move"]
    block_id: str | None = None
    block: BlockIn | None = None
    parent_id: str | None = None
    after: str | None = None
    before: str | None = None
    under_heading: str | None = None
    divider_before: bool = False


class ApplyResult(BaseModel):
    note_id: int
    revision: int
    change_set_id: str
    applied: list[str]
    suggested: list[dict[str, str]]  # {block_id, suggestion_id}
    rejected: list[dict[str, Any]]  # {block_id, reason, current: Block | None}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(8)


_COLS = (
    "id, note_id, parent_id, order_key, type, payload_json, text, author, "
    "section_key, changed_rev, changed_by, deleted_at"
)


class _Row(BaseModel):
    id: str
    note_id: int
    parent_id: str | None
    order_key: str
    type: BlockType
    props: dict[str, Any]
    content: list[dict[str, Any]] | str | None
    text: str
    author: Literal["user", "ai"]
    section_key: str | None
    changed_rev: int
    changed_by: str
    deleted_at: str | None

    def block(self) -> Block:
        return Block(
            id=self.id, note_id=self.note_id, parent_id=self.parent_id,
            type=self.type, props=self.props, content=self.content,
            text=self.text, author=self.author, section_key=self.section_key,
            changed_rev=self.changed_rev,
        )

    def state(self) -> dict[str, Any]:
        """What a history row stores: enough to put the block back."""
        return {
            "type": self.type, "props": self.props, "content": self.content,
            "author": self.author, "parent_id": self.parent_id,
            "order_key": self.order_key, "deleted_at": self.deleted_at,
        }


def _row(r: Any) -> _Row:
    payload = json.loads(r[5])
    return _Row(
        id=r[0], note_id=r[1], parent_id=r[2], order_key=r[3], type=r[4],
        props=payload.get("props") or {}, content=payload.get("content"),
        text=r[6], author=r[7], section_key=r[8], changed_rev=r[9],
        changed_by=r[10], deleted_at=r[11],
    )


class _Ctx:
    """State shared by the ops of one transaction."""

    def __init__(
        self, conn: aiosqlite.Connection, note_id: int, *, rev: int,
        actor: Actor, run_id: int | None, change_set_id: str,
    ) -> None:
        self.conn = conn
        self.note_id = note_id
        self.rev = rev
        self.actor: Actor = actor
        self.run_id = run_id
        self.change_set_id = change_set_id
        self.now = _now()
        self.applied: list[str] = []
        self.suggested: list[dict[str, str]] = []
        self.rejected: list[dict[str, Any]] = []

    def reject(self, block_id: str | None, reason: RejectReason,
               current: _Row | None = None) -> None:
        self.rejected.append({
            "block_id": block_id or "", "reason": reason,
            "current": current.block() if current else None,
        })

    def result(self) -> ApplyResult:
        return ApplyResult(
            note_id=self.note_id, revision=self.rev,
            change_set_id=self.change_set_id, applied=self.applied,
            suggested=self.suggested, rejected=self.rejected,
        )

    async def get(self, block_id: str) -> _Row | None:
        async with self.conn.execute(
            f"SELECT {_COLS} FROM note_blocks WHERE id = ? AND note_id = ?",
            (block_id, self.note_id),
        ) as cur:
            r = await cur.fetchone()
        return _row(r) if r else None

    async def live(self, block_id: str | None) -> _Row | None:
        if block_id is None:
            return None
        row = await self.get(block_id)
        return row if row and row.deleted_at is None else None

    async def siblings(self, parent_id: str | None, *,
                       live_only: bool = False) -> list[_Row]:
        where = "note_id = ? AND parent_id IS ?" + (
            " AND deleted_at IS NULL" if live_only else "")
        async with self.conn.execute(
            f"SELECT {_COLS} FROM note_blocks WHERE {where} "
            "ORDER BY order_key, id", (self.note_id, parent_id),
        ) as cur:
            return [_row(r) for r in await cur.fetchall()]

    async def descendants(self, block_id: str) -> list[_Row]:
        async with self.conn.execute(
            "WITH RECURSIVE sub(id) AS (SELECT id FROM note_blocks "
            "WHERE parent_id = ? AND note_id = ? UNION ALL SELECT b.id FROM "
            "note_blocks b JOIN sub ON b.parent_id = sub.id) "
            f"SELECT {_COLS} FROM note_blocks WHERE id IN (SELECT id FROM sub)",
            (block_id, self.note_id),
        ) as cur:
            return [_row(r) for r in await cur.fetchall()]

    async def history(self, block_id: str, op: str,
                      before: dict[str, Any] | None,
                      after: dict[str, Any] | None) -> None:
        await self.conn.execute(
            "INSERT INTO note_block_history (note_id, block_id, revision, "
            "actor, op, before_json, after_json, run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.note_id, block_id, self.rev, self.actor, op,
             json.dumps(before) if before is not None else None,
             json.dumps(after) if after is not None else None,
             self.run_id, self.now),
        )

    async def write_state(self, block_id: str, state: dict[str, Any]) -> None:
        """Set a block's content, author, position and deletion as one."""
        await self.conn.execute(
            "UPDATE note_blocks SET type = ?, payload_json = ?, text = ?, "
            "author = ?, parent_id = ?, order_key = ?, deleted_at = ?, "
            "changed_rev = ?, changed_by = ?, updated_at = ? WHERE id = ?",
            (state["type"], json.dumps({"props": state["props"],
                                        "content": state["content"]}),
             block_text(state["type"], state["props"], state["content"]),
             state["author"], state["parent_id"], state["order_key"],
             state["deleted_at"], self.rev, self.actor, self.now, block_id),
        )


def _section_ends(anchor: _Row, candidate: _Row) -> bool:
    """Whether ``candidate`` starts a section that closes ``anchor``'s."""
    if anchor.type == "section":
        return candidate.type == "section"
    if candidate.type == "section":
        return True
    if candidate.type != "heading":
        return False
    return _level(candidate) <= _level(anchor)


def _level(row: _Row) -> int:
    try:
        return int(row.props.get("level") or 1)
    except (TypeError, ValueError):
        return 1


async def _placement(
    ctx: _Ctx, op: Op, *, moving: str | None = None,
) -> tuple[str | None, str | None, str | None] | RejectReason:
    """(parent_id, prev_key, next_key) for an op's target position."""
    anchor_id = op.after or op.before or op.under_heading
    if anchor_id is not None:
        anchor = await ctx.live(anchor_id)
        if anchor is None:
            return "missing"
        if anchor_id == moving:
            return "invalid"
        parent = anchor.parent_id
    elif op.parent_id is not None:
        if await ctx.live(op.parent_id) is None:
            return "missing"
        parent = op.parent_id
    else:
        parent = None
    sibs = [s for s in await ctx.siblings(parent) if s.id != moving]
    keys = [s.order_key for s in sibs]
    last = keys[-1] if keys else None

    def before_key(k: str) -> tuple[str | None, str | None, str | None]:
        prv = [x for x in keys if x < k]
        return parent, (prv[-1] if prv else None), k

    if op.after is not None:
        a_key = next(s.order_key for s in sibs if s.id == op.after)
        nxt = [k for k in keys if k > a_key]
        return parent, a_key, (nxt[0] if nxt else None)
    if op.before is not None:
        return before_key(next(s.order_key for s in sibs if s.id == op.before))
    if op.under_heading is not None:
        # End of the heading's section: before the next live sibling that
        # opens a section of the same or a higher level, else the end.
        live = [s for s in sibs if s.deleted_at is None]
        idx = next(i for i, s in enumerate(live) if s.id == op.under_heading)
        end = next((s for s in live[idx + 1:]
                    if _section_ends(live[idx], s)), None)
        return before_key(end.order_key) if end else (parent, last, None)
    return parent, last, None


async def _insert_row(ctx: _Ctx, block_id: str, blk: BlockIn,
                      parent: str | None, key: str) -> None:
    author = "ai" if ctx.actor == "agent" else "user"
    await ctx.conn.execute(
        "INSERT INTO note_blocks (id, note_id, parent_id, order_key, type, "
        "payload_json, text, author, changed_rev, changed_by, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (block_id, ctx.note_id, parent, key, blk.type,
         json.dumps({"props": blk.props, "content": blk.content}),
         block_text(blk.type, blk.props, blk.content), author, ctx.rev,
         ctx.actor, ctx.now, ctx.now),
    )
    row = await ctx.get(block_id)
    assert row is not None
    await ctx.history(block_id, "insert", None, row.state())
    ctx.applied.append(block_id)


async def _id_in_use(ctx: _Ctx, block_id: str) -> bool:
    async with ctx.conn.execute(
        "SELECT 1 FROM note_blocks WHERE id = ?", (block_id,)) as cur:
        return await cur.fetchone() is not None


async def _place_with_divider(
    ctx: _Ctx, op: Op, parent: str | None, prev: str | None, nxt: str | None,
) -> str:
    """Insert the op's divider (if asked) and return the block's key."""
    key = key_between(prev, nxt)
    if op.divider_before:
        await _insert_row(ctx, new_block_id(), BlockIn(type="divider"),
                          parent, key)
        key = key_between(key, nxt)
    return key


async def _suggest(ctx: _Ctx, row: _Row, op: Op) -> None:
    sid = _new_id("sg_")
    kind = "update" if op.op == "update" else "delete"
    proposed = op.block.model_dump() if op.block is not None else None
    await ctx.conn.execute(
        "INSERT INTO note_suggestions (id, note_id, block_id, change_set_id, "
        "kind, proposed_json, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
        (sid, ctx.note_id, row.id, ctx.change_set_id, kind,
         json.dumps(proposed) if proposed is not None else None, ctx.now),
    )
    ctx.suggested.append({"block_id": row.id, "suggestion_id": sid})


async def _apply_one(ctx: _Ctx, op: Op, base_revision: int) -> None:
    if op.op == "insert":
        if op.block is None:
            ctx.reject(op.block_id, "invalid")
            return
        place = await _placement(ctx, op)
        if isinstance(place, str):
            ctx.reject(op.block_id or op.after or op.before or op.under_heading
                       or op.parent_id, place)
            return
        bid = op.block_id
        if bid is None or await _id_in_use(ctx, bid):
            bid = new_block_id()
        key = await _place_with_divider(ctx, op, *place)
        await _insert_row(ctx, bid, op.block, place[0], key)
        return

    if op.block_id is None:
        ctx.reject(None, "invalid")
        return
    row = await ctx.live(op.block_id)
    if row is None:
        ctx.reject(op.block_id, "missing")
        return
    if row.changed_rev > base_revision and row.changed_rev != ctx.rev:
        ctx.reject(row.id, "changed_since_base", row)
        return
    if op.op == "update" and op.block is None:
        ctx.reject(row.id, "invalid")
        return
    if (ctx.actor == "agent" and row.author == "user"
            and op.op in ("update", "delete")):
        await _suggest(ctx, row, op)
        return

    before = row.state()
    if op.op == "update":
        assert op.block is not None
        after = {**before, "type": op.block.type, "props": op.block.props,
                 "content": op.block.content}
        if ctx.actor == "user":
            after["author"] = "user"
        await ctx.write_state(row.id, after)
        await ctx.history(row.id, "update", before, after)
    elif op.op == "delete":
        subtree = [row] + [d for d in await ctx.descendants(row.id)
                           if d.deleted_at is None]
        for d in subtree:
            d_before = d.state()
            d_after = {**d_before, "deleted_at": ctx.now}
            await ctx.write_state(d.id, d_after)
            await ctx.history(d.id, "delete", d_before, d_after)
    else:  # move
        if not (op.after or op.before or op.under_heading or op.parent_id):
            ctx.reject(row.id, "invalid")
            return
        place = await _placement(ctx, op, moving=row.id)
        if isinstance(place, str):
            ctx.reject(row.id, place)
            return
        if place[0] == row.id or place[0] in {
                d.id for d in await ctx.descendants(row.id)}:
            ctx.reject(row.id, "invalid")
            return
        key = await _place_with_divider(ctx, op, *place)
        after = {**before, "parent_id": place[0], "order_key": key}
        await ctx.write_state(row.id, after)
        await ctx.history(row.id, "move", before, after)
    ctx.applied.append(row.id)


async def _begin(
    conn: aiosqlite.Connection, note_id: int, *, actor: Actor,
    run_id: int | None, summary: str, change_set_id: str | None = None,
) -> _Ctx:
    if await get_note(conn, note_id) is None:
        raise LookupError(f"note {note_id} not found")
    cs_id = change_set_id or _new_id("cs_")
    rev = await bump_revision(conn, note_id, actor=actor, summary=summary,
                              change_set_id=cs_id, run_id=run_id)
    return _Ctx(conn, note_id, rev=rev, actor=actor, run_id=run_id,
                change_set_id=cs_id)


def _outcome(ctx: _Ctx) -> str:
    return json.dumps({
        "applied": ctx.applied, "suggested": ctx.suggested,
        "rejected": [{"block_id": r["block_id"], "reason": r["reason"]}
                     for r in ctx.rejected],
    })


async def _record_change_set(ctx: _Ctx, *, base_revision: int,
                             ops_json: str) -> None:
    await ctx.conn.execute(
        "INSERT INTO note_change_sets (id, note_id, actor, run_id, "
        "base_revision, result_revision, ops_json, outcome_json, status, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?)",
        (ctx.change_set_id, ctx.note_id, ctx.actor, ctx.run_id, base_revision,
         ctx.rev, ops_json, _outcome(ctx), ctx.now),
    )


async def _apply_in_tx(
    conn: aiosqlite.Connection, note_id: int, *, base_revision: int,
    ops: list[Op], actor: Actor, run_id: int | None, summary: str,
) -> ApplyResult:
    ctx = await _begin(conn, note_id, actor=actor, run_id=run_id,
                       summary=summary)
    recorded: list[dict[str, Any]] = []
    for op in ops:
        before_applied = len(ctx.applied)
        await _apply_one(ctx, op, base_revision)
        stored = op.model_dump()
        if op.op == "insert" and len(ctx.applied) > before_applied:
            stored["block_id"] = ctx.applied[-1]  # the id the block got
        recorded.append(stored)
    await _record_change_set(ctx, base_revision=base_revision,
                             ops_json=json.dumps(recorded))
    return ctx.result()


async def apply_ops(
    conn: aiosqlite.Connection, note_id: int, *, base_revision: int,
    ops: list[Op], actor: Actor, run_id: int | None = None, summary: str = "",
) -> ApplyResult:
    """Apply ``ops`` as one revision; see the module docstring for the rules."""
    async with write_transaction(conn):
        return await _apply_in_tx(
            conn, note_id, base_revision=base_revision, ops=ops, actor=actor,
            run_id=run_id, summary=summary)


async def _load_change_set(conn: aiosqlite.Connection,
                           change_set_id: str) -> dict[str, Any]:
    async with conn.execute(
        "SELECT note_id, actor, run_id, result_revision, ops_json, status "
        "FROM note_change_sets WHERE id = ?", (change_set_id,),
    ) as cur:
        r = await cur.fetchone()
    if r is None:
        raise LookupError(f"change set {change_set_id} not found")
    return {"note_id": r[0], "actor": r[1], "run_id": r[2],
            "result_revision": r[3], "ops": json.loads(r[4]), "status": r[5]}


_UNDO_OP = {"insert": "delete", "delete": "restore", "restore": "delete",
            "update": "update", "move": "move"}


async def undo_change_set(conn: aiosqlite.Connection,
                          change_set_id: str) -> ApplyResult:
    """Revert a change set, keeping any block changed after it.

    Blocks changed later (or, for an insert, with a later-changed live
    descendant) are left as they are and reported ``changed_since_base``.
    Open suggestions of the set are withdrawn.
    """
    async with write_transaction(conn):
        cs = await _load_change_set(conn, change_set_id)
        if cs["status"] != "applied":
            raise ValueError(f"change set {change_set_id} is already undone")
        rev = cs["result_revision"]
        async with conn.execute(
            "SELECT block_id, op, before_json FROM note_block_history "
            "WHERE note_id = ? AND revision = ? ORDER BY id DESC",
            (cs["note_id"], rev),
        ) as cur:
            rows = await cur.fetchall()
        ctx = await _begin(conn, cs["note_id"], actor="user", run_id=None,
                           summary="Undo", change_set_id=change_set_id)
        kept: set[str] = set()
        for block_id, op, _ in rows:
            if block_id in kept:
                continue
            cur_row = await ctx.get(block_id)
            assert cur_row is not None
            later = cur_row.changed_rev > rev
            if not later and op == "insert":
                later = any(d.changed_rev > rev and d.deleted_at is None
                            for d in await ctx.descendants(block_id))
            if later:
                kept.add(block_id)
                ctx.reject(block_id, "changed_since_base", cur_row)
        reverted: list[str] = []
        for block_id, op, before_json in rows:
            if block_id in kept:
                continue
            cur_row = await ctx.get(block_id)
            assert cur_row is not None
            now_state = cur_row.state()
            if op in ("insert", "restore"):
                target = {**now_state, "deleted_at": ctx.now}
            else:
                target = json.loads(before_json)
            await ctx.write_state(block_id, target)
            await ctx.history(block_id, _UNDO_OP[op], now_state, target)
            if block_id not in reverted:
                reverted.append(block_id)
        ctx.applied = reverted
        await conn.execute(
            "UPDATE note_suggestions SET status = 'withdrawn' "
            "WHERE change_set_id = ? AND status = 'open'", (change_set_id,))
        await conn.execute(
            "UPDATE note_change_sets SET status = 'undone' WHERE id = ?",
            (change_set_id,))
        return ctx.result()


async def reapply_change_set(conn: aiosqlite.Connection,
                             change_set_id: str) -> ApplyResult:
    """Apply an undone change set's ops again, as a new change set."""
    async with write_transaction(conn):
        cs = await _load_change_set(conn, change_set_id)
        if cs["status"] != "undone":
            raise ValueError(f"change set {change_set_id} is not undone")
        note = await get_note(conn, cs["note_id"])
        assert note is not None
        if cs["ops"] and cs["ops"][0].get("op") == "restore":
            ids = [o["block_id"] for o in cs["ops"]]
            return await _restore_in_tx(conn, cs["note_id"], ids,
                                        actor=cs["actor"])
        ops = [Op.model_validate(o) for o in cs["ops"]]
        # The original inserts' ids still exist (soft-deleted by the undo):
        # give them fresh ids and follow the references in later ops.
        remap: dict[str, str] = {}
        for op in ops:
            if op.op == "insert" and op.block_id is not None:
                remap[op.block_id] = new_block_id()
        fields = ("block_id", "parent_id", "after", "before", "under_heading")
        ops = [op.model_copy(update={
            f: remap.get(getattr(op, f), getattr(op, f)) for f in fields})
            for op in ops]
        return await _apply_in_tx(
            conn, cs["note_id"], base_revision=note.revision, ops=ops,
            actor=cs["actor"], run_id=cs["run_id"], summary="Apply again")


async def _restore_in_tx(conn: aiosqlite.Connection, note_id: int,
                         block_ids: list[str], *, actor: Actor) -> ApplyResult:
    ctx = await _begin(conn, note_id, actor=actor, run_id=None,
                       summary="Restore")
    for bid in block_ids:
        row = await ctx.get(bid)
        if row is None:
            ctx.reject(bid, "missing")
            continue
        if row.deleted_at is None:
            ctx.reject(bid, "invalid", row)
            continue
        # The subtree deleted together with the block comes back with it.
        together = [row] + [d for d in await ctx.descendants(bid)
                            if d.deleted_at == row.deleted_at]
        for d in together:
            d_before = d.state()
            d_after = {**d_before, "deleted_at": None}
            await ctx.write_state(d.id, d_after)
            await ctx.history(d.id, "restore", d_before, d_after)
            ctx.applied.append(d.id)
    await _record_change_set(
        ctx, base_revision=ctx.rev - 1,
        ops_json=json.dumps([{"op": "restore", "block_id": b}
                             for b in block_ids]))
    return ctx.result()


async def restore_blocks(conn: aiosqlite.Connection, note_id: int,
                         block_ids: list[str], *, actor: Actor) -> ApplyResult:
    """Bring soft-deleted blocks (and their subtrees) back."""
    async with write_transaction(conn):
        return await _restore_in_tx(conn, note_id, block_ids, actor=actor)


async def resolve_suggestion(conn: aiosqlite.Connection, suggestion_id: str,
                             accept: bool) -> ApplyResult:
    """Accept (apply as the user) or reject one open suggestion."""
    async with write_transaction(conn):
        async with conn.execute(
            "SELECT note_id, block_id, change_set_id, kind, proposed_json, "
            "status FROM note_suggestions WHERE id = ?", (suggestion_id,),
        ) as cur:
            r = await cur.fetchone()
        if r is None:
            raise LookupError(f"suggestion {suggestion_id} not found")
        note_id, block_id, cs_id, kind, proposed, status = r
        if status != "open":
            raise ValueError(f"suggestion {suggestion_id} is {status}")
        note = await get_note(conn, note_id)
        assert note is not None
        if accept:
            op = Op(op="update" if kind == "update" else "delete",
                    block_id=block_id,
                    block=BlockIn.model_validate(json.loads(proposed))
                    if proposed else None)
            result = await _apply_in_tx(
                conn, note_id, base_revision=note.revision, ops=[op],
                actor="user", run_id=None, summary="Accept suggestion")
        else:
            result = ApplyResult(note_id=note_id, revision=note.revision,
                                 change_set_id=cs_id, applied=[],
                                 suggested=[], rejected=[])
        await conn.execute(
            "UPDATE note_suggestions SET status = ? WHERE id = ?",
            ("accepted" if accept else "rejected", suggestion_id))
        return result
