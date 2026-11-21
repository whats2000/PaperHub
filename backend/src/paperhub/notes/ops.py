"""The note op engine: every change to a note's blocks goes through here.

One ``apply_ops`` call is one transaction, one revision and one change set.
Rules:

* an op on a block changed after the caller's ``base_revision`` (by anyone,
  including another view of the same user) is rejected ``changed_since_base``
  with the current block; a client advances its base from each result;
* the agent never overwrites or deletes the user's words: its ``update`` /
  ``delete`` on a user-authored block (or a delete of a block with any live
  user-authored descendant) becomes one ``note_suggestions`` row;
* a subtree delete is rejected if any live descendant changed since base;
* a user edit to an AI block makes it the user's;
* deletes are soft and take the block's subtree with them;
* every applied change writes a ``note_block_history`` row.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field, field_validator, model_validator

from paperhub.db.connection import write_transaction
from paperhub.notes.inline_merge import (
    MARK_BLOCK_TYPES,
    cite_ids,
    cite_quotes,
    is_plain_table,
    is_rich,
    merge_props,
    merge_table,
    preserve_runs,
)
from paperhub.notes.mark_pieces import revive_latest
from paperhub.notes.models import (
    BLOCK_ID_PATTERN,
    Block,
    BlockType,
    check_inline,
    check_table,
    check_table_props,
)
from paperhub.notes.order import key_between
from paperhub.notes.store import (
    block_text,
    bump_revision,
    display_title,
    get_note,
    new_block_id,
)

Actor = Literal["user", "agent", "import", "system"]
RejectReason = Literal["changed_since_base", "missing", "invalid", "stale",
                       "depends_on_failed"]


class BlockIn(BaseModel):
    type: BlockType
    props: dict[str, Any] = {}
    content: list[dict[str, Any]] | dict[str, Any] | str | None = None
    section_key: str | None = None

    _inline = field_validator("content")(check_inline)

    @model_validator(mode="after")
    def _table_shape(self) -> BlockIn:
        if self.type == "table":
            if not isinstance(self.content, dict):
                raise ValueError("a table block's content must be an object")
            check_table(self.content)
            check_table_props(self.props)
        elif isinstance(self.content, dict):
            raise ValueError("only a table block has object content")
        return self

    @model_validator(mode="after")
    def _math_from_string(self) -> BlockIn:
        """A math block written as a string: the string is its LaTeX
        (surrounding ``$$`` dropped), so the block is never empty."""
        if self.type == "math" and isinstance(self.content, str):
            latex = self.content.strip()
            if latex.startswith("$$") and latex.endswith("$$") and len(latex) >= 4:
                latex = latex[2:-2].strip()
            if latex and not self.props.get("latex"):
                self.props = {**self.props, "latex": latex}
            self.content = None
        return self


class Op(BaseModel):
    op: Literal["insert", "update", "delete", "move"]
    block_id: str | None = Field(default=None, pattern=f"^{BLOCK_ID_PATTERN}$")
    block: BlockIn | None = None
    parent_id: str | None = None
    after: str | None = None
    before: str | None = None
    under_heading: str | None = None
    divider_before: bool = False


class ApplyResult(BaseModel):
    note_id: int
    revision: int
    change_set_id: str | None  # None when nothing was written (all rejected)
    applied: list[str]
    suggested: list[dict[str, str]]  # {block_id, suggestion_id}
    rejected: list[dict[str, Any]]  # {block_id, reason, current: Block | None}
    # Blocks an undo / restore brought back under their nearest live ancestor
    # because their own parent is deleted: {block_id, parent_id}.
    reparented: list[dict[str, str | None]] = []
    # Open suggestions this call withdrew (a newer one on the same block,
    # an undo of their set, or an accept that could not apply).
    withdrawn: list[str] = []
    # Inserts whose client id was already used (block ids are global, and a
    # block pasted from another note keeps its id): {from, to}, the id the
    # client sent and the one the block got. The client re-keys the block.
    renamed: list[dict[str, str]] = []
    # Plain-string updates laid over rich runs: {block_id, kept, dropped},
    # the inline nodes (citations, formulas, mentions) kept or dropped.
    inline: list[dict[str, Any]] = []


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
    content: list[dict[str, Any]] | dict[str, Any] | str | None
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
            "section_key": self.section_key,
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
        merge: bool = False,
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
        self.reparented: list[dict[str, str | None]] = []
        self.withdrawn: list[str] = []
        self.renamed: dict[str, str] = {}
        # An update merges over the stored block (agent edits, accepts).
        self.merge = merge or actor == "agent"
        self.inline: list[dict[str, Any]] = []
        # Ids of inserts that failed: an op naming one fails with them.
        self.failed: set[str] = set()
        self.op_index: int | None = None

    def reject(self, block_id: str | None, reason: RejectReason,
               current: _Row | None = None) -> None:
        entry: dict[str, Any] = {
            "block_id": block_id or "", "reason": reason,
            "current": current.block() if current else None,
        }
        if self.op_index is not None:
            entry["op"] = self.op_index
        self.rejected.append(entry)

    def result(self) -> ApplyResult:
        return ApplyResult(
            note_id=self.note_id, revision=self.rev,
            change_set_id=self.change_set_id, applied=self.applied,
            suggested=self.suggested, rejected=self.rejected,
            reparented=self.reparented, withdrawn=self.withdrawn,
            renamed=self.renamed_list(), inline=self.inline,
        )

    def renamed_list(self) -> list[dict[str, str]]:
        return [{"from": a, "to": b} for a, b in self.renamed.items()]

    def follow(self, op: Op) -> Op:
        """``op`` with every id an earlier insert of this call renamed
        replaced by the block's new id."""
        if not self.renamed:
            return op
        upd = {f: self.renamed[v] for f in
               ("block_id", "parent_id", "after", "before", "under_heading")
               if isinstance(v := getattr(op, f), str) and v in self.renamed}
        return op.model_copy(update=upd) if upd else op

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
            "changed_rev = ?, changed_by = ?, updated_at = ?, "
            "section_key = ? WHERE id = ?",
            (state["type"], json.dumps({"props": state["props"],
                                        "content": state["content"]}),
             block_text(state["type"], state["props"], state["content"]),
             state["author"], state["parent_id"], state["order_key"],
             state["deleted_at"], self.rev, self.actor, self.now,
             state.get("section_key"), block_id),
        )
        if state["type"] in MARK_BLOCK_TYPES:
            # A mark lives and dies with its block, whoever deletes or
            # brings the block back (editor, agent, undo, restore).
            if state["deleted_at"] is not None:
                await self.conn.execute(
                    "UPDATE note_marks SET deleted_at = ? "
                    "WHERE block_id = ? AND deleted_at IS NULL",
                    (state["deleted_at"], block_id))
            else:
                await self._revive_marks(block_id)

    async def _revive_marks(self, block_id: str) -> None:
        """Bring back the deleted mark of ``block_id``. One that erasing
        its last piece removed comes back with that piece, never with
        nothing to draw."""
        async with self.conn.execute(
            "SELECT id, pieces_json FROM note_marks "
            "WHERE block_id = ? AND deleted_at IS NOT NULL", (block_id,),
        ) as cur:
            rows = await cur.fetchall()
        for mark_id, raw in rows:
            pieces = json.loads(raw)
            revived = revive_latest(pieces)
            await self.conn.execute(
                "UPDATE note_marks SET deleted_at = NULL, pieces_json = ? "
                "WHERE id = ?",
                (json.dumps(revived if revived is not None else pieces), mark_id))


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
        if op.under_heading is not None and anchor.type not in (
                "heading", "section"):
            return "invalid"
        if op.parent_id is not None and op.parent_id != anchor.parent_id:
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
        "payload_json, text, author, section_key, changed_rev, changed_by, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (block_id, ctx.note_id, parent, key, blk.type,
         json.dumps({"props": blk.props, "content": blk.content}),
         block_text(blk.type, blk.props, blk.content), author,
         blk.section_key, ctx.rev,
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


async def _withdraw_open(ctx: _Ctx, block_id: str) -> None:
    """One open suggestion per block: a newer one replaces the older (and
    the result names it, so the trace shows why it went)."""
    async with ctx.conn.execute(
        "UPDATE note_suggestions SET status = 'withdrawn' "
        "WHERE note_id = ? AND block_id = ? AND status = 'open' RETURNING id",
        (ctx.note_id, block_id),
    ) as cur:
        ctx.withdrawn.extend(sorted(r[0] for r in await cur.fetchall()))


async def _suggest(ctx: _Ctx, row: _Row, op: Op) -> None:
    await _withdraw_open(ctx, row.id)
    sid = _new_id("sg_")
    kind = "update" if op.op == "update" else "delete"
    proposed = op.block.model_dump() if op.block is not None else None
    await ctx.conn.execute(
        "INSERT INTO note_suggestions (id, note_id, block_id, change_set_id, "
        "kind, proposed_json, status, created_at, base_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
        (sid, ctx.note_id, row.id, ctx.change_set_id, kind,
         json.dumps(proposed) if proposed is not None else None, ctx.now,
         block_hash(row.props, row.text)),
    )
    ctx.suggested.append({"block_id": row.id, "suggestion_id": sid})


def block_hash(props: dict[str, Any], text: str) -> str:
    """What a suggestion was made against: the block's props and text."""
    raw = json.dumps({"props": props, "text": text}, sort_keys=True,
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _merged(ctx: _Ctx, row: _Row, blk: BlockIn) -> BlockIn:
    """An update's block laid over the stored one: props merge (a mark
    block keeps its identity, type and content), and a plain string over
    rich runs keeps the runs its text did not change."""
    props = merge_props(row.type, row.props, blk.props)
    if row.type in MARK_BLOCK_TYPES:
        return blk.model_copy(update={"type": row.type, "props": props,
                                      "content": row.content})
    content = blk.content
    if (row.type == "table" and blk.type == "table"
            and isinstance(row.content, dict) and isinstance(content, dict)
            and is_plain_table(content)):
        # A table written as text (the agent's GFM), per cell: an unchanged
        # cell keeps its runs, a changed one the runs its text did not change.
        quotes = await cite_quotes(ctx.conn, cite_ids(row.content))
        table, report = merge_table(row.content, content, quotes)
        if report["kept"] or report["dropped"]:
            ctx.inline.append({"block_id": row.id, **report})
        return blk.model_copy(update={"props": props, "content": table})
    if (isinstance(content, str) and isinstance(row.content, list)
            and is_rich(row.content) and blk.type not in ("math", "codeBlock")):
        quotes = await cite_quotes(ctx.conn, cite_ids(row.content))
        runs, report = preserve_runs(row.content, content, quotes)
        if report["kept"] or report["dropped"]:
            ctx.inline.append({"block_id": row.id, **report})
        return blk.model_copy(update={"props": props, "content": runs})
    return blk.model_copy(update={"props": props})


async def _section_has_blocks(ctx: _Ctx, section: _Row) -> bool:
    """Whether live blocks sit under a ``section`` block: its children, or
    the siblings after it up to the next section."""
    if any(d.deleted_at is None for d in await ctx.descendants(section.id)):
        return True
    sibs = await ctx.siblings(section.parent_id, live_only=True)
    idx = next(i for i, s in enumerate(sibs) if s.id == section.id)
    nxt = sibs[idx + 1] if idx + 1 < len(sibs) else None
    return nxt is not None and nxt.type != "section"


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
        old = await ctx.get(bid) if bid is not None else None
        revivable = old is not None and old.deleted_at is not None
        if revivable and ctx.actor == "agent":
            # The agent never brings back words the user deleted.
            ctx.reject(bid, "invalid")
            return
        if old is not None and revivable and ctx.actor == "user":
            # The editor's undo re-inserts a deleted block under its own id:
            # bring that block back rather than mint an id the client
            # never learns about.
            key = await _place_with_divider(ctx, op, *place)
            before = old.state()
            after = {**before, "type": op.block.type,
                     "props": op.block.props, "content": op.block.content,
                     "parent_id": place[0], "order_key": key,
                     "deleted_at": None, "author": "user"}
            if op.block.section_key is not None:
                after["section_key"] = op.block.section_key
            await ctx.write_state(old.id, after)
            await ctx.history(old.id, "restore", before, after)
            ctx.applied.append(old.id)
            return
        if old is not None and not revivable:
            # A re-sent insert (a draft kept after an ambiguous failure):
            # the block is already here, so it is neither duplicated nor
            # renamed. The same words are a no-op; others are an ordinary
            # update (so a stale re-send is refused like any stale update).
            # A different type, or the agent reusing a live id, is invalid.
            if ctx.actor != "user" or op.block.type != old.type:
                ctx.reject(old.id, "invalid", old)
                return
            if (op.block.content == old.content
                    and op.block.props == old.props):
                ctx.applied.append(old.id)
                return
            await _apply_one(ctx, op.model_copy(update={"op": "update"}),
                             base_revision)
            return
        if bid is None or await _id_in_use(ctx, bid):
            new = new_block_id()
            if bid is not None:
                ctx.renamed[bid] = new
            bid = new
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
    live_desc: list[_Row] = []
    if op.op == "delete":
        live_desc = [d for d in await ctx.descendants(row.id)
                     if d.deleted_at is None]
        changed = next((d for d in live_desc if d.changed_rev > base_revision
                        and d.changed_rev != ctx.rev), None)
        if changed is not None:
            ctx.reject(row.id, "changed_since_base", changed)
            return
    if ctx.actor == "agent" and op.op in ("update", "delete") and (
            row.author == "user"
            or any(d.author == "user" for d in live_desc)):
        # Never overwrite or remove the user's words: one suggestion on the
        # target block, nothing changes until the user accepts.
        await _suggest(ctx, row, op)
        return

    before = row.state()
    if op.op == "update":
        assert op.block is not None
        blk = await _merged(ctx, row, op.block) if ctx.merge else op.block
        after = {**before, "type": blk.type, "props": blk.props,
                 "content": blk.content}
        if blk.section_key is not None:
            after["section_key"] = blk.section_key
        if ctx.actor == "user":
            after["author"] = "user"
        await ctx.write_state(row.id, after)
        await ctx.history(row.id, "update", before, after)
    elif op.op == "delete":
        for d in [row, *live_desc]:
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
        "withdrawn": ctx.withdrawn, "renamed": ctx.renamed_list(),
        "rejected": [{"block_id": r["block_id"], "reason": r["reason"]}
                     for r in ctx.rejected],
        **({"inline": ctx.inline} if ctx.inline else {}),
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
    scaffold: frozenset[str] = frozenset(), merge: bool = False,
) -> ApplyResult:
    note = await get_note(conn, note_id)
    if note is not None and base_revision > note.revision:
        raise ValueError(
            f"base_revision {base_revision} is ahead of note {note_id} "
            f"(revision {note.revision})")
    await conn.execute("SAVEPOINT apply_ops")
    ctx = await _begin(conn, note_id, actor=actor, run_id=run_id,
                       summary=summary)
    ctx.merge = ctx.merge or merge
    recorded: list[dict[str, Any]] = []
    for index, op in enumerate(ops):
        before_applied = len(ctx.applied)
        before_rejected = len(ctx.rejected)
        op = ctx.follow(op)
        ctx.op_index = index
        refs = [op.parent_id, op.after, op.before, op.under_heading]
        if op.op != "insert":
            refs.append(op.block_id)
        hung = next((r for r in refs if r is not None and r in ctx.failed), None)
        if hung is not None:
            # It names a block an earlier op of this call failed to make.
            ctx.reject(op.block_id or hung, "depends_on_failed")
        else:
            await _apply_one(ctx, op, base_revision)
        ctx.op_index = None
        if (op.op == "insert" and op.block_id is not None
                and len(ctx.rejected) > before_rejected):
            ctx.failed.add(op.block_id)
        stored = op.model_dump()
        if op.op == "insert" and len(ctx.applied) > before_applied:
            stored["block_id"] = ctx.applied[-1]  # the id the block got
        recorded.append(stored)
    await _drop_empty_scaffold(ctx, scaffold)
    if not ctx.applied and not ctx.suggested:
        # Every op was rejected: no change, so no revision and no change set.
        await conn.execute("ROLLBACK TO apply_ops")
        await conn.execute("RELEASE apply_ops")
        res = ctx.result()
        res.revision = ctx.rev - 1
        res.change_set_id = None
        return res
    await conn.execute("RELEASE apply_ops")
    await _record_change_set(ctx, base_revision=base_revision,
                             ops_json=json.dumps(recorded))
    return ctx.result()


async def _drop_empty_scaffold(ctx: _Ctx, scaffold: frozenset[str]) -> None:
    """Undo the insert of each scaffold section this call made that holds
    nothing (every op meant for it was rejected): it never happened."""
    for sid in sorted(scaffold):
        if sid not in ctx.applied:
            continue
        row = await ctx.get(sid)
        if row is None or await _section_has_blocks(ctx, row):
            continue
        await ctx.conn.execute(
            "DELETE FROM note_block_history WHERE block_id = ? AND revision = ?",
            (sid, ctx.rev))
        await ctx.conn.execute("DELETE FROM note_blocks WHERE id = ?", (sid,))
        ctx.applied.remove(sid)


async def apply_ops(
    conn: aiosqlite.Connection, note_id: int, *, base_revision: int,
    ops: list[Op], actor: Actor, run_id: int | None = None, summary: str = "",
    idempotency_key: str | None = None, scaffold: frozenset[str] = frozenset(),
) -> ApplyResult:
    """Apply ``ops`` as one revision; see the module docstring for the rules.

    ``idempotency_key`` names one burst across its retries: a key already
    stored for this note returns that burst's result and applies nothing (a
    retry after a lost response must not insert twice or revert later text).
    ``scaffold`` names section blocks inserted only to hold the other ops:
    one left empty (all its ops rejected) is not kept.
    """
    async with write_transaction(conn):
        if idempotency_key is not None:
            async with conn.execute(
                "SELECT result_json FROM note_change_sets "
                "WHERE note_id = ? AND idempotency_key = ?",
                (note_id, idempotency_key),
            ) as cur:
                row = await cur.fetchone()
            if row is not None and row[0]:
                return ApplyResult.model_validate_json(row[0])
        res = await _apply_in_tx(
            conn, note_id, base_revision=base_revision, ops=ops, actor=actor,
            run_id=run_id, summary=summary, scaffold=scaffold)
        if idempotency_key is not None and res.change_set_id is not None:
            await conn.execute(
                "UPDATE note_change_sets SET idempotency_key = ?, "
                "result_json = ? WHERE id = ?",
                (idempotency_key, res.model_dump_json(), res.change_set_id))
        return res


async def _load_change_set(conn: aiosqlite.Connection,
                           change_set_id: str) -> dict[str, Any]:
    async with conn.execute(
        "SELECT note_id, actor, run_id, result_revision, outcome_json, status "
        "FROM note_change_sets WHERE id = ?", (change_set_id,),
    ) as cur:
        r = await cur.fetchone()
    if r is None:
        raise LookupError(f"change set {change_set_id} not found")
    return {"note_id": r[0], "actor": r[1], "run_id": r[2],
            "result_revision": r[3], "outcome": json.loads(r[4]),
            "status": r[5]}


async def _set_outcome(conn: aiosqlite.Connection, change_set_id: str,
                       outcome: dict[str, Any], status: str) -> None:
    await conn.execute(
        "UPDATE note_change_sets SET outcome_json = ?, status = ? WHERE id = ?",
        (json.dumps(outcome), status, change_set_id))


def _op_between(before: dict[str, Any], after: dict[str, Any]) -> str:
    """The history op that takes a block from ``before`` to ``after``."""
    if before["deleted_at"] is not None and after["deleted_at"] is None:
        return "restore"
    if before["deleted_at"] is None and after["deleted_at"] is not None:
        return "delete"
    same_content = all(before[k] == after[k]
                       for k in ("type", "props", "content", "author"))
    return "move" if same_content else "update"


async def _settle_parents(ctx: _Ctx, block_ids: list[str]) -> None:
    """Move live blocks whose parent is deleted under the nearest live
    ancestor (or the top level), where the deleted branch stood, so a
    brought-back block is never an invisible orphan. Blocks settled behind
    the same deleted ancestor keep their relative order."""
    last_behind: dict[str, str] = {}  # deleted top id -> last settled key
    for bid in block_ids:
        row = await ctx.get(bid)
        if row is None or row.deleted_at is not None or row.parent_id is None:
            continue
        top = await ctx.get(row.parent_id)
        if top is not None and top.deleted_at is None:
            continue
        anc = await ctx.get(top.parent_id) if top and top.parent_id else None
        while anc is not None and anc.deleted_at is not None:
            top = anc
            anc = await ctx.get(anc.parent_id) if anc.parent_id else None
        new_parent = anc.id if anc is not None else None
        keys = [s.order_key for s in await ctx.siblings(new_parent)
                if s.id != bid]
        if top is not None and top.parent_id == new_parent:
            prev = last_behind.get(top.id, top.order_key)
            nxt = [k for k in keys if k > prev]
            key = key_between(prev, nxt[0] if nxt else None)
            last_behind[top.id] = key
        else:
            key = key_between(keys[-1] if keys else None, None)
        before = row.state()
        after = {**before, "parent_id": new_parent, "order_key": key}
        await ctx.write_state(bid, after)
        await ctx.history(bid, "move", before, after)
        ctx.reparented.append({"block_id": bid, "parent_id": new_parent})


_UNDO_OP = {"insert": "delete", "delete": "restore", "restore": "delete",
            "update": "update", "move": "move"}


async def undo_change_set(conn: aiosqlite.Connection,
                          change_set_id: str) -> ApplyResult:
    """Revert a change set, keeping any block changed after it.

    Blocks changed later (or, for an insert, with a later-changed live
    descendant) are left as they are and reported ``changed_since_base``.
    Open suggestions of the set are withdrawn. The set's outcome records
    what was reverted, which is exactly what Apply again puts back.
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
        kept: list[str] = []
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
                kept.append(block_id)
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
        await _settle_parents(ctx, reverted)
        ctx.applied = reverted
        async with conn.execute(
            "UPDATE note_suggestions SET status = 'withdrawn' "
            "WHERE change_set_id = ? AND status = 'open' RETURNING id",
            (change_set_id,),
        ) as cur:
            withdrawn = sorted(r[0] for r in await cur.fetchall())
        ctx.withdrawn.extend(withdrawn)
        outcome = {**cs["outcome"], "undo": {
            "revision": ctx.rev, "reverted": reverted, "kept": kept,
            "withdrawn": withdrawn}}
        await _set_outcome(conn, change_set_id, outcome, "undone")
        return ctx.result()


async def reapply_change_set(conn: aiosqlite.Connection,
                             change_set_id: str) -> ApplyResult:
    """Apply again: put back exactly what the Undo reverted.

    Each reverted block gets its after-state from the set's history again
    (same ids, no re-run of the ops). Blocks the Undo kept, or changed
    after the Undo, are left alone and reported ``changed_since_base``.
    The result is a new, undoable change set; the original records it in
    ``reapplied_by`` and cannot be applied again a second time.
    """
    async with write_transaction(conn):
        cs = await _load_change_set(conn, change_set_id)
        undo = cs["outcome"].get("undo")
        if cs["status"] != "undone" or undo is None:
            raise ValueError(f"change set {change_set_id} is not undone")
        if cs["outcome"].get("reapplied_by"):
            raise ValueError(f"change set {change_set_id} was already applied "
                             "again")
        async with conn.execute(
            "SELECT block_id, after_json FROM note_block_history "
            "WHERE note_id = ? AND revision = ? ORDER BY id",
            (cs["note_id"], cs["result_revision"]),
        ) as cur:
            final: dict[str, dict[str, Any]] = {
                bid: json.loads(after) for bid, after in await cur.fetchall()}
        ctx = await _begin(conn, cs["note_id"], actor=cs["actor"],
                           run_id=cs["run_id"], summary="Apply again")
        for bid, target in final.items():
            cur_row = await ctx.get(bid)
            assert cur_row is not None
            if bid not in undo["reverted"] or cur_row.changed_rev > undo["revision"]:
                ctx.reject(bid, "changed_since_base", cur_row)
                continue
            before = cur_row.state()
            await ctx.write_state(bid, target)
            await ctx.history(bid, _op_between(before, target), before, target)
            ctx.applied.append(bid)
        await _settle_parents(ctx, ctx.applied)
        await _reopen_suggestions(ctx, undo)
        await _record_change_set(
            ctx, base_revision=ctx.rev - 1,
            ops_json=json.dumps([{"op": "reapply",
                                  "change_set_id": change_set_id}]))
        outcome = {**cs["outcome"], "reapplied_by": ctx.change_set_id}
        await _set_outcome(conn, change_set_id, outcome, "undone")
        return ctx.result()


async def _reopen_suggestions(ctx: _Ctx, undo: dict[str, Any]) -> None:
    """Reopen the suggestions an Undo withdrew, under the new change set,
    unless their block changed after the Undo."""
    for sid in undo.get("withdrawn", []):
        async with ctx.conn.execute(
            "SELECT block_id FROM note_suggestions WHERE id = ? "
            "AND status = 'withdrawn'", (sid,),
        ) as cur:
            r = await cur.fetchone()
        if r is None:
            continue
        row = await ctx.live(r[0])
        if row is None:
            ctx.reject(r[0], "missing")
            continue
        if row.changed_rev > undo["revision"]:
            if all(x["block_id"] != row.id for x in ctx.rejected):
                ctx.reject(row.id, "changed_since_base", row)
            continue
        await _withdraw_open(ctx, row.id)
        await ctx.conn.execute(
            "UPDATE note_suggestions SET status = 'open', change_set_id = ? "
            "WHERE id = ?", (ctx.change_set_id, sid))
        ctx.suggested.append({"block_id": row.id, "suggestion_id": sid})


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
    await _settle_parents(ctx, ctx.applied)
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
    """Accept (apply as the user) or reject one open suggestion.

    An accepted suggestion that can no longer apply (its block was deleted
    meanwhile, …) is ``withdrawn`` and the result carries the rejection.
    """
    async with write_transaction(conn):
        async with conn.execute(
            "SELECT note_id, block_id, change_set_id, kind, proposed_json, "
            "status, base_hash FROM note_suggestions WHERE id = ?",
            (suggestion_id,),
        ) as cur:
            r = await cur.fetchone()
        if r is None:
            raise LookupError(f"suggestion {suggestion_id} not found")
        note_id, block_id, cs_id, kind, proposed, status, base_hash = r
        if status != "open":
            raise ValueError(f"suggestion {suggestion_id} is {status}")
        note = await get_note(conn, note_id)
        assert note is not None
        async with conn.execute(
            "SELECT 1 FROM note_blocks WHERE id = ? AND note_id = ? "
            "AND deleted_at IS NULL", (block_id, note_id),
        ) as cur:
            live = await cur.fetchone() is not None
        if accept and not live:
            # Nothing to apply: withdraw without a revision or change set.
            await conn.execute(
                "UPDATE note_suggestions SET status = 'withdrawn' WHERE id = ?",
                (suggestion_id,))
            return ApplyResult(
                note_id=note_id, revision=note.revision, change_set_id=cs_id,
                applied=[], suggested=[],
                rejected=[{"block_id": block_id, "reason": "missing",
                           "current": None}],
                withdrawn=[suggestion_id])
        if accept and base_hash is not None:
            stale = await _stale(conn, suggestion_id, note_id, block_id,
                                 base_hash)
            if stale is not None:
                return ApplyResult(
                    note_id=note_id, revision=note.revision,
                    change_set_id=None, applied=[], suggested=[],
                    rejected=[{"block_id": block_id, "reason": "stale",
                               "current": stale}])
        if accept:
            op = Op(op="update" if kind == "update" else "delete",
                    block_id=block_id,
                    block=BlockIn.model_validate(json.loads(proposed))
                    if proposed else None)
            result = await _apply_in_tx(
                conn, note_id, base_revision=note.revision, ops=[op],
                actor="user", run_id=None, summary="Accept suggestion",
                merge=True)
            new_status = ("accepted" if block_id in result.applied
                          else "withdrawn")
            if new_status == "withdrawn":
                result.withdrawn = [*result.withdrawn, suggestion_id]
        else:
            result = ApplyResult(note_id=note_id, revision=note.revision,
                                 change_set_id=cs_id, applied=[],
                                 suggested=[], rejected=[])
            new_status = "rejected"
        await conn.execute(
            "UPDATE note_suggestions SET status = ? WHERE id = ?",
            (new_status, suggestion_id))
        return result


async def _stale(conn: aiosqlite.Connection, suggestion_id: str, note_id: int,
                 block_id: str, base_hash: str) -> Block | None:
    """The block as it is now when its props or text changed since the
    suggestion was made, else None. A stale suggestion is re-based on the
    current block (it stays open and is shown against the current text),
    so the user's next Accept applies it: the action is idempotent."""
    async with conn.execute(
        f"SELECT {_COLS} FROM note_blocks WHERE id = ? AND note_id = ?",  # noqa: S608
        (block_id, note_id),
    ) as cur:
        r = await cur.fetchone()
    assert r is not None
    row = _row(r)
    now = block_hash(row.props, row.text)
    if now == base_hash:
        return None
    await conn.execute(
        "UPDATE note_suggestions SET base_hash = ? WHERE id = ?",
        (now, suggestion_id))
    return row.block()


_STRUCTURAL = frozenset({"section", "divider", "toc"})


def _state_text(state: dict[str, Any] | None) -> str:
    if not state:
        return ""
    return block_text(str(state.get("type") or ""), state.get("props") or {},
                      state.get("content"))


async def change_set_detail(conn: aiosqlite.Connection,
                            change_set_id: str) -> dict[str, Any]:
    """A change set as the chat card shows it.

    ``head`` is the set live now: Apply again makes a new set, so the chain
    of ``reapplied_by`` is followed to its end. ``changes`` lists what the
    set did (blocks added, updated, deleted; sections, dividers and tables
    of contents left
    out) and its suggestions with the block's text now and the proposal,
    one per block, from whichever set of the chain holds it now.
    """
    cs = await _load_change_set(conn, change_set_id)
    chain = [change_set_id]
    head = cs
    while head["outcome"].get("reapplied_by"):
        nxt = str(head["outcome"]["reapplied_by"])
        if nxt in chain:  # never loop on a bad row
            break
        chain.append(nxt)
        head = await _load_change_set(conn, nxt)
    note = await get_note(conn, cs["note_id"])
    assert note is not None
    changes: list[dict[str, Any]] = []
    async with conn.execute(
        "SELECT block_id, op, before_json, after_json FROM note_block_history "
        "WHERE note_id = ? AND revision = ? ORDER BY id",
        (cs["note_id"], cs["result_revision"]),
    ) as cur:
        rows = await cur.fetchall()
    kinds = {"insert": "added", "update": "updated", "delete": "deleted"}
    for block_id, op, before_json, after_json in rows:
        if op not in kinds:
            continue
        before = json.loads(before_json) if before_json else None
        after = json.loads(after_json) if after_json else None
        shown = after if op != "delete" else before
        if shown is None or shown.get("type") in _STRUCTURAL:
            continue
        item: dict[str, Any] = {"kind": kinds[op], "block_id": block_id,
                                "text": _state_text(shown)}
        if op == "update":
            item["before"] = _state_text(before)
        changes.append(item)
    marks = ",".join("?" for _ in chain)
    async with conn.execute(
        "SELECT s.block_id, s.kind, s.proposed_json, s.status, b.text "  # noqa: S608
        "FROM note_suggestions s LEFT JOIN note_blocks b ON b.id = s.block_id "
        f"WHERE s.change_set_id IN ({marks}) ORDER BY s.created_at, s.rowid",
        chain,
    ) as cur:
        sug_rows = await cur.fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for block_id, kind, proposed, status, now_text in sug_rows:
        latest[block_id] = {
            "kind": "suggested", "block_id": block_id, "op": kind,
            "before": now_text or "",
            "text": _state_text(json.loads(proposed)) if proposed else "",
            "status": status}
    changes.extend(latest.values())
    return {"id": change_set_id, "note_id": cs["note_id"],
            "status": cs["status"],
            "reapplied_by": cs["outcome"].get("reapplied_by"),
            "head": {"id": chain[-1], "status": head["status"]},
            "note": {"target_kind": note.target_kind,
                     "target_id": note.target_id,
                     "title": await display_title(conn, note)},
            "changes": changes}
