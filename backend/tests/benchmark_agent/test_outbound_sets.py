"""Schema of the outbound-review eval sets (H5a Task 14)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.agent.outbound_eval import all_must_not, case_variables, leaked, sent_text, windows
from benchmark.agent.prompts import load_variant

SETS = Path(__file__).resolve().parents[2] / "benchmark" / "agent" / "sets"
PROMPTS = Path(__file__).resolve().parents[2] / "benchmark" / "agent" / "prompts"


def _load(name: str) -> list[dict]:
    return [json.loads(x) for x in (SETS / f"outbound_{name}.jsonl").read_text(
        encoding="utf-8").splitlines() if x]


def test_leak_set_schema() -> None:
    rows = _load("leak")
    assert len(rows) >= 24
    assert len({r["case_id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["private_material"] and r["query"] and r["must_not_contain"]
        assert r["kind"] in {"web_search", "s2_search", "arxiv_search"}
        case_variables(r)  # builds with the checkpoint helper


def test_leak_set_has_long_spans() -> None:
    spans = [m for r in _load("leak") for m in r["must_not_contain"] if len(m.split()) >= 8]
    assert len(spans) >= 5


def test_benign_set_schema() -> None:
    rows = _load("benign")
    assert len(rows) >= 24
    assert len({r["case_id"] for r in rows}) == len(rows)
    assert any(not r["private_material"] for r in rows)
    for r in rows:
        assert r["query"] and "must_not_contain" not in r
        case_variables(r)


@pytest.mark.parametrize("ver", ["v1", "v1a", "v1b", "v1d", "v1e"])
def test_variants_load_and_format(ver: str) -> None:
    system, user = load_variant("outbound_review", ver, prompts_dir=PROMPTS)
    row = _load("leak")[0]
    assert "<query>" in user.format(**case_variables(row))
    assert "ask" in system


def test_deterministic_leak_check() -> None:
    assert sent_text("allow", "q", None) == "q"
    assert sent_text("rewrite", "q", " r ") == "r"
    assert sent_text("ask", "q", None) is None and sent_text("block", "q", "x") is None
    assert leaked("The Kestrel  report", ["kestrel"]) == ["kestrel"]
    assert leaked(None, ["kestrel"]) == []


def test_benign_set_has_boundary_cases() -> None:
    assert len([r for r in _load("benign") if r["case_id"].startswith("boundary-")]) >= 6


def test_auto_windows_cover_private_text() -> None:
    en = "one two three four five six seven eight nine"
    assert windows(en) == ["one two three four five six seven eight", "two three four five six seven eight nine"]
    ja = "NMC811セルを四十五度で三百サイクル運転した後容量が低下した"
    assert any(w in ja for w in windows(ja)) and len([w for w in windows(ja) if len(w) == 12]) > 3
    case = {"private_material": [{"text": en}], "must_not_contain": ["x"]}
    assert leaked("see two three four five six seven eight nine", all_must_not(case))
    assert leaked("two three four", all_must_not(case)) == []


def test_examples_do_not_overlap_eval_cases() -> None:
    import re

    rows = [r for n in ("leak", "benign") for r in _load(n)]
    queries = {r["query"].lower() for r in rows}
    # private-material phrases: every 4-word window and every distinctive token
    phrases: set[str] = set()
    tokens: set[str] = set()
    for r in rows:
        for p in r["private_material"]:
            words = re.findall(r"\w+", p["text"].lower())
            phrases |= {" ".join(words[i:i + 4]) for i in range(len(words) - 3)}
            # distinctive tokens: names/codes (capitalised or with a digit), not prose
            tokens |= {w.lower() for w in re.findall(r"(?:[A-Z][A-Za-z]+|\w*\d\w*)", p["text"])}
        tokens |= {t.lower() for t in r["sensitive_terms"]}
    tokens -= {"internal", "next", "our", "the", "we", "in", "it", "a", "of", "to", "and", "complaints",
               "cohort", "customer", "project", "tape", "dr", "chapter", "draft", "private"}
    for ver in ("v1", "v1a", "v1b", "v1c", "v1d", "v1e"):
        system, _ = load_variant("outbound_review", ver, prompts_dir=PROMPTS)
        ex = system.split("## Examples", 1)[1].lower()
        assert not any(q in ex for q in queries), ver
        assert not any(ph in " ".join(re.findall(r"\w+", ex)) for ph in phrases), ver
        found = [t for t in tokens if re.search(rf"{re.escape(t)}", ex)]
        assert not found, (ver, found)


def test_discover_framing_eval_smoke() -> None:
    """The R26 script touches private names of research_pipeline; fail loudly on a refactor."""
    from benchmark.agent import discover_framing_eval as d
    from paperhub.agents import research_pipeline as rp

    for name in ("_DISCOVER_TOOL_SCHEMA", "_parse_canonical_identity", "search_results_message",
                 "_notice_message"):
        assert hasattr(rp, name), name
    hits = d.canned_hits("T", "1234.56789", inject=True)
    assert "<search_results>" in rp.search_results_message(hits)
    assert "PaperHub notice" in rp._notice_message(d._BLOCKED)
    ident = rp._parse_canonical_identity('{"title": "T", "confidence": "high"}')
    assert ident is not None and ident.title == "T"
    msgs = d._start("Mamba paper")
    assert [m["role"] for m in msgs] == ["system", "user"] and "Mamba paper" in msgs[1]["content"]


def test_production_rescore_applies_the_private_title_guard() -> None:
    from benchmark.agent.outbound_eval import production_sent

    case = {"query": "perplexity 12.4 sparse model", "sensitive_terms": [],
            "private_material": [{"kind": "document", "ref": "91",
                                  "title": "Sparse Routing for Efficient Sequence Models",
                                  "text": "x"}]}
    rewrite = {"sent": "sparse routing for efficient sequence models arxiv"}
    assert production_sent(case, rewrite) == (None, "span")
    assert production_sent(dict(case, query=case["private_material"][0]["title"]),
                           {"sent": "x"}) == (None, "span")
    assert production_sent(case, {"sent": "sparse mixture of experts"}) == (
        "sparse mixture of experts", "review")


def test_production_rescore_any_order_only_for_a_rewrite() -> None:
    from benchmark.agent.outbound_eval import production_sent

    title = "Gated Memory Replay for Streaming Video Captioning"
    words = "video captioning gated memory replay streaming"
    case = {"query": words, "sensitive_terms": [],
            "private_material": [{"kind": "document", "ref": "95", "title": title, "text": "x"}]}
    assert production_sent(case, {"sent": words}) == (words, "review")  # allow: the query
    assert production_sent(dict(case, query="captioner 131.7"), {"sent": words}) == (None, "span")


def test_benign_set_has_common_word_title_boundaries() -> None:
    ids = {r["case_id"] for r in _load("benign")}
    assert {"boundary-title-common-words", "boundary-title-four-of-five"} <= ids
