"""Plan H3 Task 3: domain tags (normalise + alias, manual-safe) and the
server-side colour assignment (Ruling O3)."""
from __future__ import annotations

import asyncio
import json
from itertools import combinations
from pathlib import Path

import aiosqlite
import pytest

from paperhub.db.connection import write_transaction
from paperhub.ideation.domains import (
    PALETTE_SIZE,
    PASSING_PAIRS,
    assign_color_indices,
    assign_domains,
    normalize_domain,
)

_REPO = Path(__file__).resolve().parents[2]
_BACKEND_PALETTE = _REPO / "backend/src/paperhub/ideation/domain_palette.json"
_FRONTEND_PALETTE = _REPO / "frontend/src/lib/domainPalette.json"


async def _doc(conn: aiosqlite.Connection, n: int) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path) VALUES (?, 'pdf_upload', ?, ?, '', '', '')",
        (f"pdf:{n}", f"{n:064d}", f"Doc {n}"),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def _tags(conn: aiosqlite.Connection, pid: int) -> list[tuple[str, int]]:
    async with conn.execute(
        "SELECT d.name, dd.manual FROM document_domains dd "
        "JOIN domains d ON d.id = dd.domain_id WHERE dd.paper_content_id = ? "
        "ORDER BY d.name", (pid,),
    ) as cur:
        return [(str(r[0]), int(r[1])) for r in await cur.fetchall()]


async def _colors(conn: aiosqlite.Connection) -> dict[str, int | None]:
    async with conn.execute("SELECT name, color_index FROM domains") as cur:
        return {str(r[0]): r[1] for r in await cur.fetchall()}


def test_normalize() -> None:
    assert normalize_domain("  Computer   Vision ") == "computer vision"
    assert normalize_domain("CV") == "computer vision"
    assert normalize_domain("nlp") == "natural language processing"
    assert normalize_domain("RL") == "reinforcement learning"
    assert normalize_domain("ML Theory") == normalize_domain("machine learning theory")


async def test_alias_and_case_map_to_one_domain(migrated_db: aiosqlite.Connection) -> None:
    a = await _doc(migrated_db, 1)
    b = await _doc(migrated_db, 2)
    await assign_domains(migrated_db, a, ["Computer Vision"])
    await assign_domains(migrated_db, b, ["cv", " computer vision "])
    async with migrated_db.execute("SELECT COUNT(*) FROM domains") as cur:
        assert (await cur.fetchone() or [0])[0] == 1
    assert await _tags(migrated_db, b) == [("Computer Vision", 0)]


async def test_reassign_replaces_auto_tags(migrated_db: aiosqlite.Connection) -> None:
    a = await _doc(migrated_db, 1)
    await assign_domains(migrated_db, a, ["Robotics", "NLP"])
    await assign_domains(migrated_db, a, ["robotics"])
    assert await _tags(migrated_db, a) == [("Robotics", 0)]


async def test_manual_tags_survive(migrated_db: aiosqlite.Connection) -> None:
    a = await _doc(migrated_db, 1)
    await assign_domains(migrated_db, a, ["Robotics"])
    # The user edits the document's domains by hand.
    await migrated_db.execute(
        "INSERT INTO domains (name) VALUES ('Control Theory')")
    await migrated_db.execute(
        "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
        "SELECT ?, id, 1 FROM domains WHERE name = 'Control Theory'", (a,))
    await migrated_db.commit()
    await assign_domains(migrated_db, a, ["Computer Vision"])
    # Manual row kept; no auto tags added to a manually-tagged document.
    assert ("Control Theory", 1) in await _tags(migrated_db, a)
    assert ("Computer Vision", 0) not in await _tags(migrated_db, a)


async def test_manual_row_never_downgraded(migrated_db: aiosqlite.Connection) -> None:
    a = await _doc(migrated_db, 1)
    await migrated_db.execute("INSERT INTO domains (name) VALUES ('Robotics')")
    await migrated_db.execute(
        "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
        "VALUES (?, 1, 1)", (a,))
    await migrated_db.commit()
    await assign_domains(migrated_db, a, ["robotics"])
    assert await _tags(migrated_db, a) == [("Robotics", 1)]


def test_palette_files_identical_and_pairs_valid() -> None:
    backend = json.loads(_BACKEND_PALETTE.read_text(encoding="utf-8"))
    if _FRONTEND_PALETTE.exists():
        assert json.loads(_FRONTEND_PALETTE.read_text(encoding="utf-8")) == backend
    assert len(backend["light"]) == len(backend["dark"]) == PALETTE_SIZE == 8
    for a, b in PASSING_PAIRS:
        assert 0 <= a < b < PALETTE_SIZE


async def test_colors_kept_and_adjacent_pairs_pass(
    migrated_db: aiosqlite.Connection,
) -> None:
    a = await _doc(migrated_db, 1)
    b = await _doc(migrated_db, 2)
    await assign_domains(migrated_db, a, ["Computer Vision", "Robotics"])
    await assign_color_indices(migrated_db)
    first = await _colors(migrated_db)
    assert first["Computer Vision"] == 0
    assert first["Robotics"] is not None

    await assign_domains(migrated_db, b, ["NLP"])
    await migrated_db.execute(
        "INSERT INTO citation_edges (source_id, target_id) VALUES (?, ?)", (b, a))
    await migrated_db.commit()
    await assign_color_indices(migrated_db)
    second = await _colors(migrated_db)
    # Existing colours never repaint.
    assert second["Computer Vision"] == first["Computer Vision"]
    assert second["Robotics"] == first["Robotics"]
    # NLP is adjacent (citation edge) to both; every adjacent pair passes.
    for x, y in combinations(second.values(), 2):
        assert x is not None and y is not None
        assert tuple(sorted((x, y))) in PASSING_PAIRS


async def test_more_than_palette_folds_to_other(migrated_db: aiosqlite.Connection) -> None:
    names = [f"Field {i}" for i in range(PALETTE_SIZE + 1)]
    for i, n in enumerate(names):
        d = await _doc(migrated_db, 100 + i)
        await assign_domains(migrated_db, d, [n])
    await assign_color_indices(migrated_db)
    first = await _colors(migrated_db)
    assert sorted(first[n] for n in names[:PALETTE_SIZE]) == list(range(PALETTE_SIZE))
    assert first[names[PALETTE_SIZE]] is None  # the 9th is gray Other
    await assign_color_indices(migrated_db)
    assert await _colors(migrated_db) == first  # a second pass repaints nothing


async def test_concurrent_color_passes_never_share_a_slot(
    migrated_db: aiosqlite.Connection,
) -> None:
    for i in range(6):
        d = await _doc(migrated_db, 200 + i)
        await assign_domains(migrated_db, d, [f"Field {i}"])
    await asyncio.gather(*(assign_color_indices(migrated_db) for _ in range(4)))
    slots = [c for c in (await _colors(migrated_db)).values() if c is not None]
    assert len(slots) == 6 and len(set(slots)) == 6


async def test_drifted_auto_domain_is_deleted_and_frees_its_slot(
    migrated_db: aiosqlite.Connection,
) -> None:
    a = await _doc(migrated_db, 1)
    b = await _doc(migrated_db, 2)
    await assign_domains(migrated_db, a, ["Old Field"])
    await assign_domains(migrated_db, b, ["Keep"])
    await assign_color_indices(migrated_db)
    old_slot = (await _colors(migrated_db))["Old Field"]
    await assign_domains(migrated_db, a, ["New Field"])  # re-analysis drifted
    await assign_color_indices(migrated_db)
    colors = await _colors(migrated_db)
    assert "Old Field" not in colors
    assert colors["New Field"] == old_slot  # the freed slot is reused
    # A domain still used by another document's MANUAL row is kept.
    await migrated_db.execute(
        "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
        "SELECT ?, id, 1 FROM domains WHERE name = 'New Field'", (b,))
    await migrated_db.commit()
    await assign_domains(migrated_db, a, ["Other Field"])
    assert "New Field" in await _colors(migrated_db)


async def test_documentless_domain_gives_its_slot_back(
    migrated_db: aiosqlite.Connection,
) -> None:
    a = await _doc(migrated_db, 1)
    await migrated_db.execute("INSERT INTO domains (name, color_index) VALUES ('Stale', 0)")
    await migrated_db.commit()
    await assign_domains(migrated_db, a, ["Fresh"])
    await assign_color_indices(migrated_db)
    colors = await _colors(migrated_db)
    assert colors["Stale"] is None and colors["Fresh"] == 0


@pytest.mark.parametrize("raw", ["", "   "])
async def test_blank_names_ignored(migrated_db: aiosqlite.Connection, raw: str) -> None:
    a = await _doc(migrated_db, 1)
    await assign_domains(migrated_db, a, [raw])
    assert await _tags(migrated_db, a) == []


async def test_user_curated_domain_survives_the_orphan_cleanup(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A renamed (manual=1) domain is never deleted when re-analysis drops it."""
    a = await _doc(migrated_db, 1)
    await assign_domains(migrated_db, a, ["Old Field"])
    await migrated_db.execute(
        "UPDATE domains SET name = 'My Field', manual = 1 WHERE name = 'Old Field'")
    await migrated_db.commit()
    await assign_domains(migrated_db, a, ["New Field"])  # re-analysis drifted
    assert "My Field" in await _colors(migrated_db)


async def test_public_wrappers_fail_fast_inside_a_write_transaction(
    migrated_db: aiosqlite.Connection,
) -> None:
    """The write lock is not re-entrant: a nested public call would hang.
    Awaited directly — the guard is per task, so a wrapper run in a NEW task
    (``wait_for``/``gather``) is not detected."""
    a = await _doc(migrated_db, 1)
    async with write_transaction(migrated_db):
        with pytest.raises(RuntimeError, match="_tx"):
            await assign_domains(migrated_db, a, ["Robotics"])
        with pytest.raises(RuntimeError, match="_tx"):
            await assign_color_indices(migrated_db)


async def test_migrate_adds_domains_manual_to_an_old_db(tmp_path: Path) -> None:
    from paperhub.db.migrate import apply_schema

    async with aiosqlite.connect(tmp_path / "old.db") as conn:
        await conn.execute(
            "CREATE TABLE domains (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE, color_index INTEGER, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')))")
        await conn.execute("INSERT INTO domains (name) VALUES ('Robotics')")
        await conn.commit()
        await apply_schema(conn)
        async with conn.execute("SELECT manual FROM domains") as cur:
            assert [r[0] for r in await cur.fetchall()] == [0]
        async with conn.execute("SELECT COUNT(*) FROM domain_aliases") as cur:
            assert (await cur.fetchone() or (None,))[0] == 0


async def test_child_task_spawned_in_a_transaction_may_tag_later(
    migrated_db: aiosqlite.Connection,
) -> None:
    """The guard is per task: a child copies the context but is not inside."""
    a = await _doc(migrated_db, 1)
    committed = asyncio.Event()

    async def child() -> None:
        await committed.wait()
        await assign_domains(migrated_db, a, ["Robotics"])
        await assign_color_indices(migrated_db)

    async with write_transaction(migrated_db):
        task = asyncio.create_task(child())
    committed.set()
    await asyncio.wait_for(task, 5)
    assert await _tags(migrated_db, a) == [("Robotics", 0)]



# ---------------------------------------------------------------- QA I8


def test_near_duplicate_fields_share_one_canonical_domain() -> None:
    from paperhub.ideation.domains import display_domain

    for a, b in (("Audio", "Audio Processing"), ("Genomics", "Functional Genomics"),
                 ("Computational Biology", "Cell Biology"), ("Optics", "Remote Sensing"),
                 ("Cloud Computing", "Distributed Systems"),
                 ("Machine Translation", "NLP"), ("Virtual Reality", "HCI"),
                 ("Generative Models", "Machine Learning Theory"),
                 ("Power Systems Engineering", "Energy Systems")):
        assert normalize_domain(a) == normalize_domain(b), (a, b)
    assert display_domain("functional genomics") == "Computational Biology"
    assert display_domain("Audio") == "Audio and Speech"
    assert display_domain("Underwater Basket Weaving") == "Underwater Basket Weaving"


async def test_a_card_tags_at_most_two_domains(migrated_db: aiosqlite.Connection) -> None:
    from paperhub.ideation.domains import MAX_DOMAINS

    a = await _doc(migrated_db, 1)
    await assign_domains(migrated_db, a, ["NLP", "Genomics", "Audio"])
    assert MAX_DOMAINS == 2
    assert await _tags(migrated_db, a) == [("Computational Biology", 0),
                                           ("Natural Language Processing", 0)]


async def test_an_alias_lands_on_the_canonical_row_not_an_older_variant(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A legacy 'Functional Genomics' row (made before the alias) must not
    capture the canonical name."""
    await migrated_db.execute("INSERT INTO domains (name) VALUES ('Functional Genomics')")
    await migrated_db.execute("INSERT INTO domains (name) VALUES ('Computational Biology')")
    await migrated_db.commit()
    a = await _doc(migrated_db, 1)
    await assign_domains(migrated_db, a, ["Genomics"])
    assert await _tags(migrated_db, a) == [("Computational Biology", 0)]


async def test_consolidate_merges_auto_variants_and_caps(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.ideation.domains import consolidate_domains

    a = await _doc(migrated_db, 1)
    b = await _doc(migrated_db, 2)
    c = await _doc(migrated_db, 3)
    # Legacy rows, as tagged before the aliases existed.
    for name in ("Audio Processing", "Audio", "Robotics", "Control Theory"):
        await migrated_db.execute("INSERT INTO domains (name) VALUES (?)", (name,))
    await migrated_db.execute("UPDATE domains SET manual = 1 WHERE name = 'Control Theory'")
    rows = [(a, "Robotics", 0), (a, "Audio Processing", 0), (a, "Audio", 0),
            (b, "Audio", 0), (c, "Control Theory", 1), (c, "Audio", 1)]
    for pid, name, manual in rows:
        await migrated_db.execute(
            "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
            "SELECT ?, id, ? FROM domains WHERE name = ?", (pid, manual, name))
    await migrated_db.commit()
    plan = await consolidate_domains(migrated_db, dry_run=True)
    assert plan["renamed_or_merged"] == {"Audio": "Audio and Speech",
                                         "Audio Processing": "Audio and Speech"}
    assert await _tags(migrated_db, b) == [("Audio", 0)]  # dry run: unchanged
    await consolidate_domains(migrated_db, dry_run=False)
    assert await _tags(migrated_db, a) == [("Audio and Speech", 0), ("Robotics", 0)]
    assert await _tags(migrated_db, b) == [("Audio and Speech", 0)]
    # a manual tag keeps its flag; a user-curated domain keeps its name
    assert await _tags(migrated_db, c) == [("Audio and Speech", 1), ("Control Theory", 1)]
    again = await consolidate_domains(migrated_db, dry_run=False)
    assert again["renamed_or_merged"] == {} and again["capped"] == {}


def test_stored_tags_keep_machine_learning_only_as_a_primary_field() -> None:
    """Every paper in an ML library is ML: as a second tag it is filler that
    merges every hull on the graph (the first card v2 run tagged 40 of 61)."""
    from paperhub.ideation.domains import stored_tags

    assert stored_tags(["NLP", "Machine Learning"]) == ["Natural Language Processing"]
    assert stored_tags(["Deep Learning", "NLP", "Genomics"]) == [
        "Machine Learning", "Natural Language Processing"]
    assert stored_tags(["Robotics", "ML", "CV"]) == ["Robotics", "Computer Vision"]
    assert stored_tags(["Audio", "Audio Processing"]) == ["Audio and Speech"]


async def test_assign_drops_a_secondary_machine_learning_tag(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.ideation.domains import consolidate_domains

    a = await _doc(migrated_db, 1)
    await assign_domains(migrated_db, a, ["BERT field: NLP", "Machine Learning"])
    assert await _tags(migrated_db, a) == [("BERT field: NLP", 0)]
    b = await _doc(migrated_db, 2)
    for name in ("Robotics", "Machine Learning"):
        await migrated_db.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (name,))
        await migrated_db.execute(
            "INSERT INTO document_domains (paper_content_id, domain_id, manual) "
            "SELECT ?, id, 0 FROM domains WHERE name = ?", (b, name))
    await migrated_db.commit()
    plan = await consolidate_domains(migrated_db, dry_run=False)
    assert plan["capped"] == {str(b): ["Machine Learning"]}
    assert await _tags(migrated_db, b) == [("Robotics", 0)]
