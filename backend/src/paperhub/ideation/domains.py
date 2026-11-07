"""Domain tags for library documents + their server-side colour (Plan H3).

``assign_domains`` maps a card's free-text domain names onto ``domains`` rows:
names are casefolded, whitespace-collapsed and run through an alias map, so
"Computer Vision" and "cv" land on one domain. It replaces a document's AUTO
tags only — a ``manual=1`` row is never touched, and a document with any manual
tag gets no auto tags at all (the user's edit wins over every re-analysis).
A user rename or merge records the old name in ``domain_aliases``, so a later
card naming the old domain lands on the renamed / merged-into one. A
domain that loses its last document row in such a re-tag is deleted — unless
it is user-curated (``domains.manual = 1``: renamed, a merge target, or created
by a manual edit), which is kept even with no documents left.

``assign_color_indices`` (Ruling O3) gives each uncoloured domain a palette
slot. A colour follows its domain for life while the domain is on the graph:
an existing ``color_index`` is kept. A new domain takes the lowest slot 0..7
not used by another domain WITH documents, whose pair with every ADJACENT
coloured domain is in ``PASSING_PAIRS`` (validated by the dataviz
``validate_palette.js`` in both themes); adjacency = sharing a document, or a
citation edge between their documents. No such slot (or all 8 used) → NULL,
drawn as gray "Other". A gray domain is re-tried on every call, so it gains a
colour when a slot frees up (intended). A domain with no documents gives its
slot back (``color_index`` → NULL), so stale domains never hold the 8 slots.

Concurrency: the ``*_tx`` variants run inside the caller's
``write_transaction`` (they never commit); the public wrappers open one. The
transaction holds the process-wide ``_DB_WRITE_LOCK`` + ``BEGIN IMMEDIATE``, so
colour assignment's read → compute → write is serialised (no duplicate slot);
the conditional ``WHERE color_index IS NULL`` write is a second guard. Never
call a public wrapper from inside a ``write_transaction`` (the lock is not
re-entrant; the wrappers raise ``RuntimeError`` instead of hanging) — call the
``*_tx`` variant. Do not run a public wrapper on a
connection a Tracer is writing to concurrently (the tracer's bare
``INSERT``+``commit`` would land inside the transaction); ``cards`` uses its
own connection for that reason.

The palette + passing pairs live in ``domain_palette.json``, identical to the
frontend's ``lib/domainPalette.json``.
"""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

import aiosqlite

from paperhub.db.connection import in_write_transaction, write_transaction

_PALETTE = json.loads(
    (files("paperhub.ideation") / "domain_palette.json").read_text(encoding="utf-8")
)
PALETTE_SIZE: int = len(_PALETTE["light"])
PASSING_PAIRS: frozenset[tuple[int, int]] = frozenset(
    (min(a, b), max(a, b)) for a, b in _PALETTE["passing_pairs"]
)

# The canonical fields: normalized key -> display name. The card prompt names
# the same list (ideation_card v2), so most tags land here directly; the alias
# map below folds the near-duplicates a card or an older card produced
# ("Audio" / "Audio Processing", "Genomics" / "Functional Genomics").
_CANONICAL: dict[str, str] = {
    "computer vision": "Computer Vision",
    "natural language processing": "Natural Language Processing",
    "robotics": "Robotics",
    "reinforcement learning": "Reinforcement Learning",
    "machine learning": "Machine Learning",
    "computational biology": "Computational Biology",
    "audio and speech": "Audio and Speech",
    "optics and imaging": "Optics and Imaging",
    "information retrieval": "Information Retrieval",
    "distributed systems": "Distributed Systems",
    "security": "Security",
    "energy systems": "Energy Systems",
    "transportation": "Transportation",
    "human-computer interaction": "Human-Computer Interaction",
    "manufacturing": "Manufacturing",
}
_ALIASES: dict[str, str] = {
    "cv": "computer vision",
    "vision": "computer vision",
    "ir": "information retrieval",
    "nlp": "natural language processing",
    "machine translation": "natural language processing",
    "computational linguistics": "natural language processing",
    "rl": "reinforcement learning",
    "robot learning": "robotics",
    "ml": "machine learning",
    "ml theory": "machine learning",
    "learning theory": "machine learning",
    "machine learning theory": "machine learning",
    "deep learning": "machine learning",
    "artificial intelligence": "machine learning",
    "ai": "machine learning",
    "generative models": "machine learning",
    "generative modeling": "machine learning",
    "representation learning": "machine learning",
    "multimodal modeling": "machine learning",
    "multimodal learning": "machine learning",
    "genomics": "computational biology",
    "functional genomics": "computational biology",
    "bioinformatics": "computational biology",
    "cell biology": "computational biology",
    "biology": "computational biology",
    "audio": "audio and speech",
    "audio processing": "audio and speech",
    "speech": "audio and speech",
    "speech processing": "audio and speech",
    "optics": "optics and imaging",
    "imaging systems": "optics and imaging",
    "computational imaging": "optics and imaging",
    "remote sensing": "optics and imaging",
    "cloud computing": "distributed systems",
    "network security": "security",
    "computer security": "security",
    "cybersecurity": "security",
    "power systems": "energy systems",
    "power systems engineering": "energy systems",
    "energy": "energy systems",
    "traffic and transportation research": "transportation",
    "hci": "human-computer interaction",
    "human computer interaction": "human-computer interaction",
    "virtual reality": "human-computer interaction",
}

MAX_DOMAINS = 2
"""Most auto domains a document gets: its primary field plus one more. Three
let a general method collect every field it was benchmarked on (Mamba was
tagged NLP | Genomics | Audio)."""


def _fold(name: str) -> str:
    return " ".join(name.split()).casefold()


def normalize_domain(name: str) -> str:
    """Casefold + collapse whitespace, then resolve a known alias."""
    folded = _fold(name)
    return _ALIASES.get(folded, folded)


def display_domain(name: str) -> str:
    """The canonical display name of ``name`` (unknown names keep theirs)."""
    key = normalize_domain(name)
    return _CANONICAL.get(key, " ".join(name.split()))


_display = display_domain

_PRIMARY_ONLY = frozenset({"machine learning"})
"""Fields kept only as a document's primary field. In an ML-heavy library
every paper is "Machine Learning"; as a second tag it is filler that merges
every domain hull on the graph (the first card-v2 run added it to 40 of 61)."""


def stored_tags(names: list[str]) -> list[str]:
    """The canonical tags ``assign_domains`` stores for ``names``: folded,
    de-duplicated, a primary-only field dropped unless first, at most
    ``MAX_DOMAINS``."""
    out: list[str] = []
    for raw in names:
        if not raw.strip():
            continue
        name = display_domain(raw)
        if name in out or (out and normalize_domain(name) in _PRIMARY_ONLY):
            continue
        out.append(name)
        if len(out) == MAX_DOMAINS:
            break
    return out


async def _domain_index(conn: aiosqlite.Connection) -> dict[str, int]:
    async with conn.execute("SELECT id, name FROM domains ORDER BY id") as cur:
        rows = await cur.fetchall()
    out: dict[str, int] = {}
    # The row already named canonically wins its key, so a legacy variant
    # ("Functional Genomics") never captures the canonical field's tags.
    ordered = sorted(rows, key=lambda r: (display_domain(str(r[1])) != str(r[1]), int(r[0])))
    for did, name in ordered:
        out.setdefault(normalize_domain(str(name)), int(did))
    # User renames/merges: an old name keeps resolving to the domain it now
    # belongs to. A live domain's own name wins over an alias.
    async with conn.execute("SELECT name_norm, domain_id FROM domain_aliases") as cur:
        for key, did in await cur.fetchall():
            out.setdefault(str(key), int(did))
    return out


async def _domain_id(conn: aiosqlite.Connection, display: str) -> int:
    """Id of the domain named ``display``, created if missing (race-free)."""
    await conn.execute(
        "INSERT INTO domains (name) VALUES (?) ON CONFLICT(name) DO NOTHING", (display,))
    async with conn.execute("SELECT id FROM domains WHERE name = ?", (display,)) as cur:
        row = await cur.fetchone()
    if row is None:  # pragma: no cover — the insert above guarantees a row
        raise RuntimeError(f"domain {display!r} vanished")
    return int(row[0])


def _not_nested(name: str) -> None:
    """Fail fast instead of deadlocking on the non-re-entrant write lock."""
    if in_write_transaction():
        raise RuntimeError(
            f"{name} opens its own write transaction; inside one call {name}_tx"
        )


async def has_domains(conn: aiosqlite.Connection, paper_content_id: int) -> bool:
    async with conn.execute(
        "SELECT 1 FROM document_domains WHERE paper_content_id = ? LIMIT 1",
        (paper_content_id,),
    ) as cur:
        return await cur.fetchone() is not None


async def assign_domains_tx(
    conn: aiosqlite.Connection, paper_content_id: int, names: list[str],
) -> None:
    """``assign_domains`` inside the caller's write transaction (no commit)."""
    async with conn.execute(
        "SELECT 1 FROM document_domains WHERE paper_content_id = ? AND manual = 1 "
        "LIMIT 1", (paper_content_id,),
    ) as cur:
        if await cur.fetchone() is not None:
            return
    async with conn.execute(
        "SELECT domain_id FROM document_domains WHERE paper_content_id = ?",
        (paper_content_id,),
    ) as cur:
        previous = {int(r[0]) for r in await cur.fetchall()}
    index = await _domain_index(conn)
    wanted: list[int] = []
    for raw in stored_tags(names):
        key = normalize_domain(raw)
        did = index.get(key)
        if did is None:
            did = await _domain_id(conn, _display(raw))
            index[key] = did
        if did not in wanted:
            wanted.append(did)
        if len(wanted) == MAX_DOMAINS:
            break
    await conn.execute(
        "DELETE FROM document_domains WHERE paper_content_id = ? AND manual = 0",
        (paper_content_id,),
    )
    await conn.executemany(
        "INSERT OR IGNORE INTO document_domains (paper_content_id, domain_id, manual) "
        "VALUES (?, ?, 0)", [(paper_content_id, d) for d in wanted],
    )
    await drop_orphan_domains_tx(conn, previous - set(wanted))


async def drop_orphan_domains_tx(
    conn: aiosqlite.Connection, domain_ids: set[int],
) -> None:
    """Delete those of ``domain_ids`` left with no document that are not
    user-curated (``manual = 0``). Inside the caller's write transaction."""
    if domain_ids:
        await conn.executemany(
            "DELETE FROM domains WHERE id = ? AND manual = 0 AND NOT EXISTS "
            "(SELECT 1 FROM document_domains WHERE domain_id = domains.id)",
            [(d,) for d in sorted(domain_ids)],
        )


async def manual_domain_id_tx(conn: aiosqlite.Connection, name: str) -> int:
    """Id of the domain a user typed (an alias/case match reuses the existing
    one, else it is created), marked user-curated. Inside the caller's write
    transaction; ``name`` must not be blank."""
    did = (await _domain_index(conn)).get(normalize_domain(name))
    if did is None:
        did = await _domain_id(conn, _display(name))
    await conn.execute("UPDATE domains SET manual = 1 WHERE id = ?", (did,))
    return did


async def rename_domain_tx(
    conn: aiosqlite.Connection, domain_id: int, old_name: str, new_name: str,
) -> None:
    """Rename a domain (user-curated from now on) and alias its old name to
    it, so a re-analysis that still says the old name keeps the tag here.
    Inside the caller's write transaction."""
    old_key, new_key = normalize_domain(old_name), normalize_domain(new_name)
    await conn.execute("DELETE FROM domain_aliases WHERE name_norm = ?", (new_key,))
    await conn.execute(
        "UPDATE domains SET name = ?, manual = 1 WHERE id = ?", (new_name, domain_id))
    if old_key != new_key:
        await conn.execute(
            "INSERT OR REPLACE INTO domain_aliases (name_norm, domain_id) VALUES (?, ?)",
            (old_key, domain_id),
        )


async def merge_domain_tx(
    conn: aiosqlite.Connection, domain_id: int, old_name: str, into: int,
) -> None:
    """Move a domain's tags onto ``into`` (a tag keeps its manual flag), point
    its name and aliases at ``into``, delete it, and mark ``into``
    user-curated. Inside the caller's write transaction."""
    await conn.execute(
        "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
        "SELECT paper_content_id, ?, manual FROM document_domains WHERE domain_id = ? "
        "ON CONFLICT(paper_content_id, domain_id) "
        "DO UPDATE SET manual = MAX(manual, excluded.manual)",
        (into, domain_id),
    )
    await conn.execute(
        "UPDATE domain_aliases SET domain_id = ? WHERE domain_id = ?", (into, domain_id))
    await conn.execute(
        "INSERT OR REPLACE INTO domain_aliases (name_norm, domain_id) VALUES (?, ?)",
        (normalize_domain(old_name), into),
    )
    await conn.execute("DELETE FROM document_domains WHERE domain_id = ?", (domain_id,))
    await conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
    await conn.execute("UPDATE domains SET manual = 1 WHERE id = ?", (into,))


async def consolidate_domains_tx(
    conn: aiosqlite.Connection, *, dry_run: bool = False,
) -> dict[str, dict[str, Any]]:
    """Fold auto-tagged legacy domains into their canonical row and cap each
    document's auto tags at ``MAX_DOMAINS`` (QA I8). A user-curated domain
    (``manual = 1``) and a document with a manual tag are never touched.
    Returns ``{"renamed_or_merged": {old name: canonical}, "capped": {doc id:
    [dropped names]}}``; idempotent. Inside the caller's write transaction."""
    async with conn.execute("SELECT id, name, manual FROM domains ORDER BY id") as cur:
        domains = [(int(r[0]), str(r[1]), int(r[2])) for r in await cur.fetchall()]
    moved: dict[str, str] = {}
    by_name = {n: d for d, n, _m in domains}
    for did, name, manual in domains:
        canon = display_domain(name)
        if manual or canon == name:
            continue
        moved[name] = canon
        if dry_run:
            continue
        into = by_name.get(canon)
        if into is None:
            await conn.execute("UPDATE domains SET name = ? WHERE id = ?", (canon, did))
            by_name[canon] = did
            continue
        # Tags move with their manual flag (a user's tag stays the user's).
        await conn.execute(
            "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
            "SELECT paper_content_id, ?, manual FROM document_domains WHERE domain_id = ? "
            "ON CONFLICT(paper_content_id, domain_id) "
            "DO UPDATE SET manual = MAX(manual, excluded.manual)",
            (into, did),
        )
        await conn.execute(
            "UPDATE domain_aliases SET domain_id = ? WHERE domain_id = ?", (into, did))
        await conn.execute("DELETE FROM document_domains WHERE domain_id = ?", (did,))
        await conn.execute("DELETE FROM domains WHERE id = ?", (did,))
    capped: dict[str, list[str]] = {}
    async with conn.execute(
        "SELECT dd.paper_content_id, dd.domain_id, d.name FROM document_domains dd "
        "JOIN domains d ON d.id = dd.domain_id WHERE dd.manual = 0 "
        "AND NOT EXISTS (SELECT 1 FROM document_domains m WHERE "
        "m.paper_content_id = dd.paper_content_id AND m.manual = 1) "
        "ORDER BY dd.paper_content_id, dd.rowid",
    ) as cur:
        tags: dict[int, list[tuple[int, str]]] = {}
        for pid, did, name in await cur.fetchall():
            tags.setdefault(int(pid), []).append((int(did), str(name)))
    for pid, rows in tags.items():
        # In a dry run the legacy names are not folded yet: count distinct
        # canonical fields, as the real run would see them.
        seen: list[str] = []
        extra: list[tuple[int, str]] = []
        for did, name in rows:
            canon = display_domain(name)
            if canon in seen:
                continue
            if seen and normalize_domain(canon) in _PRIMARY_ONLY:
                extra.append((did, name))
            elif len(seen) < MAX_DOMAINS:
                seen.append(canon)
            else:
                extra.append((did, name))
        if extra:
            capped[str(pid)] = [n for _d, n in extra]
            if not dry_run:
                await conn.executemany(
                    "DELETE FROM document_domains WHERE paper_content_id = ? "
                    "AND domain_id = ? AND manual = 0", [(pid, d) for d, _n in extra])
    if not dry_run:
        await conn.execute(
            "DELETE FROM domains WHERE manual = 0 AND NOT EXISTS "
            "(SELECT 1 FROM document_domains WHERE domain_id = domains.id)")
        await assign_color_indices_tx(conn)
    return {"renamed_or_merged": moved, "capped": capped}


async def consolidate_domains(
    conn: aiosqlite.Connection, *, dry_run: bool = False,
) -> dict[str, dict[str, Any]]:
    """``consolidate_domains_tx`` in its own write transaction."""
    _not_nested("consolidate_domains")
    async with write_transaction(conn):
        return await consolidate_domains_tx(conn, dry_run=dry_run)


async def assign_domains(
    conn: aiosqlite.Connection, paper_content_id: int, names: list[str],
) -> None:
    """Replace the document's auto tags with ``names`` (manual tags win)."""
    _not_nested("assign_domains")
    async with write_transaction(conn):
        await assign_domains_tx(conn, paper_content_id, names)


async def _adjacency(conn: aiosqlite.Connection) -> dict[int, set[int]]:
    async with conn.execute(
        "SELECT a.domain_id, b.domain_id FROM document_domains a "
        "JOIN document_domains b ON a.paper_content_id = b.paper_content_id "
        "WHERE a.domain_id != b.domain_id "
        "UNION "
        "SELECT a.domain_id, b.domain_id FROM citation_edges e "
        "JOIN document_domains a ON a.paper_content_id = e.source_id "
        "JOIN document_domains b ON b.paper_content_id = e.target_id "
        "WHERE a.domain_id != b.domain_id"
    ) as cur:
        rows = await cur.fetchall()
    adj: dict[int, set[int]] = {}
    for x, y in rows:
        adj.setdefault(int(x), set()).add(int(y))
        adj.setdefault(int(y), set()).add(int(x))
    return adj


def _passes(a: int, b: int) -> bool:
    return (min(a, b), max(a, b)) in PASSING_PAIRS


async def assign_color_indices_tx(conn: aiosqlite.Connection) -> None:
    """``assign_color_indices`` inside the caller's write transaction."""
    # A domain with no documents is off the graph: give its slot back.
    await conn.execute(
        "UPDATE domains SET color_index = NULL WHERE color_index IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM document_domains WHERE domain_id = domains.id)"
    )
    async with conn.execute(
        "SELECT id, color_index FROM domains WHERE EXISTS "
        "(SELECT 1 FROM document_domains WHERE domain_id = domains.id) ORDER BY id"
    ) as cur:
        rows = [(int(r[0]), r[1]) for r in await cur.fetchall()]
    color: dict[int, int] = {d: int(c) for d, c in rows if c is not None}
    pending = [d for d, c in rows if c is None]
    if not pending:
        return
    adj = await _adjacency(conn)
    for did in pending:
        used = set(color.values())
        neighbours = [color[n] for n in adj.get(did, set()) if n in color]
        slot = next(
            (i for i in range(PALETTE_SIZE)
             if i not in used and all(_passes(i, c) for c in neighbours)),
            None,
        )
        if slot is None:
            continue
        cur2 = await conn.execute(
            "UPDATE domains SET color_index = ? WHERE id = ? AND color_index IS NULL",
            (slot, did),
        )
        if cur2.rowcount:
            color[did] = slot


async def assign_color_indices(conn: aiosqlite.Connection) -> None:
    """Colour every uncoloured domain (existing colours are never repainted)."""
    _not_nested("assign_color_indices")
    async with write_transaction(conn):
        await assign_color_indices_tx(conn)
