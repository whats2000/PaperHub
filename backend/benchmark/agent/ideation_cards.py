"""Pre-sweep build step for the ideation eval set (Plan H3).

Replaces the ``@cards_from_db`` sentinel in each case's ``cards_xml`` with the
library's mechanism cards rendered exactly as ``generate_ideas`` renders them
(``paperhub.ideation.agent.render_cards_xml``), minus the ids in
``expect.exclude_docs`` — or, when ``expect.board_docs`` is set, only those
ids (a small explore board; a listed id with no card fails the build). Reads
cards + domains from a DB (read-only) — point it at a COPY of the workspace
whose cards were built with the deploy card model.

    uv run python -m benchmark.agent.ideation_cards --db <copy>/paperhub.db \
        --in benchmark/agent/corpus/ideation.core.jsonl \
        --out benchmark/agent/corpus/ideation.core.cards.jsonl

A repair case (``rejected_xml`` = ``FIRST_ROUND_REJECTED``) replays
``generate_ideas``' second call, made when every first-round proposal was
dropped: ``expect.first_round`` is validated against the board with the agent's
own check and the drops, with their reasons, are rendered as ``rejected_xml``
(``status="invalid"``), exactly as the agent renders them.

The output holds every document's card text (private documents included), so
``*.cards.jsonl`` is gitignored.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from benchmark.agent.stages import CARDS_FROM_DB
from paperhub.ideation.agent import (
    BoardCard,
    _as_rejected,
    _check,
    card_sections,
    render_cards_xml,
    render_rejected_xml,
)
from paperhub.ideation.models import MechanismCard, ProposedIdea

FIRST_ROUND_REJECTED = "@rejected_from_first_round"


def load_board(db_path: Path) -> list[BoardCard]:
    """Every document whose card cites a section, in id order (what
    ``generate_ideas`` puts on the board)."""
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        domains: dict[int, list[str]] = {}
        for pid, name in db.execute(
            "SELECT dd.paper_content_id, d.name FROM document_domains dd "
            "JOIN domains d ON d.id = dd.domain_id ORDER BY dd.paper_content_id, dd.rowid",
        ):
            domains.setdefault(int(pid), []).append(str(name))
        rows = db.execute(
            "SELECT pc.id, pc.title, mc.card_json FROM paper_content pc "
            "JOIN mechanism_cards mc ON mc.paper_content_id = pc.id ORDER BY pc.id",
        ).fetchall()
    finally:
        db.close()
    board = [
        BoardCard(doc=int(pid), title=str(title or ""), domains=domains.get(int(pid), []),
                  card=MechanismCard.model_validate_json(str(card)))
        for pid, title, card in rows
    ]
    return [c for c in board if card_sections(c.card)]  # as generate_ideas: uncitable cards stay off


def fill_case(case: dict[str, Any], board: list[BoardCard]) -> dict[str, Any]:
    # Cases written before round 8 lack the variables generate_ideas added
    # since (existing_xml, response_language): fill the agent's defaults.
    variables = {"existing_xml": "", "response_language": "", **case["variables"]}
    if variables.get("cards_xml") == CARDS_FROM_DB:
        expect = case.get("expect") or {}
        exclude = set(expect.get("exclude_docs") or [])
        cards = [c for c in board if c.doc not in exclude]
        if expect.get("board_docs") is not None:
            keep = set(expect["board_docs"])
            missing = sorted(keep - {c.doc for c in cards})
            if missing:
                raise SystemExit(f"{case['case_id']}: board_docs without a card: {missing}")
            cards = [c for c in cards if c.doc in keep]
        variables["cards_xml"] = render_cards_xml(cards)
        if variables.get("rejected_xml") == FIRST_ROUND_REJECTED:
            variables["rejected_xml"] = _first_round_xml(case, cards)
        # What the scorer replays generate_ideas' validation against.
        expect = {**expect, "mode": variables.get("mode", "problem"),
                  "board": {str(c.doc): {"domains": c.domains, "title": c.title,
                                         "sections": card_sections(c.card)}
                            for c in cards}}
        return {**case, "variables": variables, "expect": expect}
    return {**case, "variables": variables}


def _first_round_xml(case: dict[str, Any], cards: list[BoardCard]) -> str:
    """``generate_ideas``' repair input: the first round's drops with reasons."""
    mode = case["variables"].get("mode", "problem")
    by_doc = {c.doc: c.card for c in cards}
    domains = {c.doc: c.domains for c in cards}
    dropped: list[dict[str, Any]] = []
    for rank, raw in enumerate(case["expect"]["first_round"]):
        idea = ProposedIdea.model_validate(raw)
        reason, _s, _t = _check(idea, mode=mode, cards=by_doc, domains=domains)
        if reason is None:
            raise SystemExit(f"{case['case_id']}: first-round idea {rank} would be kept, "
                             "so generate_ideas would not run a repair round")
        dropped.append({"rank": rank, **idea.model_dump(), "reason": reason})
    return render_rejected_xml(_as_rejected(dropped))


def build(db_path: Path, src: Path, out: Path) -> int:
    board = load_board(db_path)
    if not board:
        raise SystemExit(f"no mechanism cards in {db_path}")
    lines = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    filled = [fill_case(c, board) for c in lines]
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in filled),
                   encoding="utf-8")
    return len(filled)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    n = build(Path(a.db), Path(a.src), Path(a.out))
    print(f"wrote {n} cases to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
