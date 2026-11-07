"""Plan H3 Task 5: the pre-sweep step that fills ``@cards_from_db``."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from benchmark.agent.ideation_cards import build
from benchmark.agent.stages import CARDS_FROM_DB
from paperhub.ideation.models import CitedField, MechanismCard


def _card(name: str) -> str:
    return MechanismCard(
        problem=CitedField(text=f"{name} problem", sections=["1 Intro"]),
        mechanism=CitedField(text=f"{name} mechanism", sections=["3 Method"]),
        domains=["Vision"],
    ).model_dump_json()


async def test_sentinel_is_replaced_minus_excluded_docs(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    for n in (1, 2):
        await migrated_db.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) VALUES (?, ?, 'arxiv', ?, ?, '', '', '')",
            (n, f"arxiv:{n}", str(n), f"Doc {n}"),
        )
        await migrated_db.execute(
            "INSERT INTO mechanism_cards (paper_content_id, card_json, digest_hash) "
            "VALUES (?, ?, 'h')", (n, _card(f"doc{n}")),
        )
    await migrated_db.execute("INSERT INTO domains (id, name) VALUES (1, 'Computer Vision')")
    await migrated_db.execute(
        "INSERT INTO document_domains (paper_content_id, domain_id) VALUES (2, 1)")
    await migrated_db.commit()

    src = tmp_path / "in.jsonl"
    case = {"case_id": "c", "stage": "ideation",
            "variables": {"problem": "p", "tried": "", "cards_xml": CARDS_FROM_DB,
                          "rejected_xml": "", "mode": "problem"},
            "expect": {"source_doc": 2, "exclude_docs": [1]}}
    src.write_text(json.dumps(case) + "\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    assert build(tmp_path / "paperhub.db", src, out) == 1

    filled = json.loads(out.read_text(encoding="utf-8"))
    xml = filled["variables"]["cards_xml"]
    assert '<card doc="2" title="Doc 2" domains="Computer Vision">' in xml
    assert 'doc="1"' not in xml and "doc2 mechanism" in xml
    expect = filled["expect"]
    assert {k: v for k, v in expect.items() if k not in ("board", "mode")} == case["expect"]
    # what the scorer replays generate_ideas' validation against: the board's
    # cards only, with their domains (primary first) and cited sections
    assert expect["mode"] == "problem"
    assert expect["board"] == {"2": {"domains": ["Computer Vision"], "title": "Doc 2",
                                     "sections": ["1 Intro", "3 Method"]}}


async def _seed_three(db: aiosqlite.Connection) -> None:
    for n in (1, 2, 3):
        await db.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) VALUES (?, ?, 'arxiv', ?, ?, '', '', '')",
            (n, f"arxiv:{n}", str(n), f"Doc {n}"),
        )
        await db.execute(
            "INSERT INTO mechanism_cards (paper_content_id, card_json, digest_hash) "
            "VALUES (?, ?, 'h')", (n, _card(f"doc{n}")),
        )
    await db.commit()


def _explore_case(board: list[int]) -> dict[str, object]:
    return {"case_id": "b", "stage": "ideation",
            "variables": {"problem": "", "tried": "", "cards_xml": CARDS_FROM_DB,
                          "rejected_xml": "", "mode": "explore"},
            "expect": {"source_doc": 1, "target_doc": 3, "board_docs": board}}


async def test_board_docs_keeps_only_the_listed_cards(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    await _seed_three(migrated_db)
    src = tmp_path / "in.jsonl"
    src.write_text(json.dumps(_explore_case([3, 1])) + "\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    build(tmp_path / "paperhub.db", src, out)
    xml = json.loads(out.read_text(encoding="utf-8"))["variables"]["cards_xml"]
    assert 'doc="1"' in xml and 'doc="3"' in xml and 'doc="2"' not in xml
    assert xml.index('doc="1"') < xml.index('doc="3"')  # board stays in id order
    assert set(json.loads(out.read_text(encoding="utf-8"))["expect"]["board"]) == {"1", "3"}


async def test_board_doc_without_a_card_fails_loudly(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    await _seed_three(migrated_db)
    src = tmp_path / "in.jsonl"
    src.write_text(json.dumps(_explore_case([1, 3, 9])) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="9"):
        build(tmp_path / "paperhub.db", src, tmp_path / "out.jsonl")


async def test_repair_sentinel_renders_the_first_round_drops_like_generate_ideas(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    """A repair case replays generate_ideas' second call: its first-round
    proposals are validated against the board and the dropped ones, with
    the agent's own reasons, become ``rejected_xml``."""
    from benchmark.agent.ideation_cards import FIRST_ROUND_REJECTED

    for n in (1, 2):
        await migrated_db.execute(
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) VALUES (?, ?, 'arxiv', ?, ?, '', '', '')",
            (n, f"arxiv:{n}", str(n), f"Doc {n}"),
        )
        await migrated_db.execute(
            "INSERT INTO mechanism_cards (paper_content_id, card_json, digest_hash) "
            "VALUES (?, ?, 'h')", (n, _card(f"doc{n}")),
        )
        await migrated_db.execute("INSERT INTO domains (id, name) VALUES (?, ?)",
                                  (n, "Computer Vision" if n == 1 else "Robotics"))
    await migrated_db.execute(
        "INSERT INTO document_domains (paper_content_id, domain_id) VALUES (1, 1), (2, 1)")
    await migrated_db.commit()
    first = [
        {"source_doc": 1, "source_section": "3 Method", "target_doc": 2,
         "target_section": "1 Intro", "title": "Same field", "rationale": "r"},
        {"source_doc": 2, "source_section": "Nope", "target_doc": 1,
         "target_section": "1 Intro", "title": "Bad cite", "rationale": "r"},
    ]
    case = {"case_id": "r", "stage": "ideation",
            "variables": {"problem": "", "tried": "", "cards_xml": CARDS_FROM_DB,
                          "rejected_xml": FIRST_ROUND_REJECTED, "mode": "explore"},
            "expect": {"min_valid": 1, "first_round": first}}
    src = tmp_path / "in.jsonl"
    src.write_text(json.dumps(case) + "\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    build(tmp_path / "paperhub.db", src, out)
    xml = json.loads(out.read_text(encoding="utf-8"))["variables"]["rejected_xml"]
    assert xml.count("<idea ") == 2 and 'status="invalid"' in xml
    assert "same domain" in xml and "not cited by its card" in xml
    assert "Same field" in xml and "Bad cite" in xml


async def test_repair_sentinel_fails_loudly_when_a_first_round_idea_is_valid(
    migrated_db: aiosqlite.Connection, tmp_path: Path,
) -> None:
    """The repair round only runs when EVERY proposal was dropped."""
    from benchmark.agent.ideation_cards import FIRST_ROUND_REJECTED

    await migrated_db.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "source_path, source_dir_path, html_path) VALUES (1, 'a:1', 'arxiv', '1', 'D', '', '', '')")
    await migrated_db.execute(
        "INSERT INTO mechanism_cards (paper_content_id, card_json, digest_hash) "
        "VALUES (1, ?, 'h')", (_card("d"),))
    await migrated_db.commit()
    case = {"case_id": "r", "stage": "ideation",
            "variables": {"problem": "p", "tried": "", "cards_xml": CARDS_FROM_DB,
                          "rejected_xml": FIRST_ROUND_REJECTED, "mode": "problem"},
            "expect": {"min_valid": 1, "first_round": [
                {"source_doc": 1, "source_section": "3 Method", "target_doc": None,
                 "target_section": None, "title": "ok", "rationale": "r"}]}}
    src = tmp_path / "in.jsonl"
    src.write_text(json.dumps(case) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="would be kept"):
        build(tmp_path / "paperhub.db", src, tmp_path / "out.jsonl")
