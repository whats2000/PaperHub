"""H4 scenario benchmark: multi-turn cases, history sending, --compare and the
merge-gate maths (G-a..G-d)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from benchmark import driver, gate, runner
from benchmark.config import Turn, history_for, load_config
from benchmark.scorer import Grounding

SCENARIO_CFG = """
[benchmark]
name = "h4-scenarios"
suite = "scenarios"

[[cases]]
id = "s1-a"
scenario = 1
prompt = "Give me more detail on the experiment method."
turns = [
  { user = "Can GISC help YOLOSeg?", assistant = "Plausibly [chunk:101]." },
  { user = "Where would it go?", assistant = "In the neck." },
]

[[cases]]
id = "chit"
kind = "chitchat"
prompt = "thanks!"
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cases.toml"
    p.write_text(text, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_config_parses_turns_scenario_kind_and_suite(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, SCENARIO_CFG))
    assert cfg.suite == "scenarios"
    a, chit = cfg.cases
    assert a.scenario == 1
    assert a.turns == [
        Turn(user="Can GISC help YOLOSeg?", assistant="Plausibly [chunk:101]."),
        Turn(user="Where would it go?", assistant="In the neck."),
    ]
    assert a.kind is None
    assert chit.kind == "chitchat"
    assert chit.scenario is None and chit.turns == []


def test_history_for_flattens_turns_into_chat_history(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, SCENARIO_CFG))
    assert history_for(cfg.cases[0]) == [
        {"role": "user", "content": "Can GISC help YOLOSeg?"},
        {"role": "assistant", "content": "Plausibly [chunk:101]."},
        {"role": "user", "content": "Where would it go?"},
        {"role": "assistant", "content": "In the neck."},
    ]
    assert history_for(cfg.cases[1]) == []


def test_suite_defaults_to_none(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, '[[cases]]\nid = "x"\nprompt = "hi"\n'))
    assert cfg.suite is None


@pytest.mark.parametrize("bad", [
    'scenario = 9',
    'scenario = 0',
    'turns = [{ user = "only a user" }]',
    'turns = [{ user = "", assistant = "x" }]',
    'turns = "not a list"',
])
def test_config_rejects_bad_scenario_or_turns(tmp_path: Path, bad: str) -> None:
    text = f'[[cases]]\nid = "x"\nprompt = "hi"\n{bad}\n'
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, text))


def test_regression_suite_is_marked() -> None:
    cfg = load_config(Path(__file__).parents[2] / "benchmark" / "cases.example.toml")
    assert cfg.suite == "regression"


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

class _Stream:
    def __init__(self, sse: str) -> None:
        self._sse = sse

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_text(self) -> list[str]:
        return [self._sse]


def _capture_stream(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_stream(method: str, url: str, *, json: dict[str, Any], timeout: float) -> _Stream:
        seen["url"] = url
        seen["json"] = json
        return _Stream('event: session\ndata: {"run_id": 7}\n\n'
                       'event: final\ndata: {"content": "ok"}\n\n')

    monkeypatch.setattr("benchmark.driver.httpx.stream", fake_stream)
    return seen


def test_driver_sends_history(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_stream(monkeypatch)
    monkeypatch.setattr(driver, "BASE", "http://127.0.0.1:8100")
    hist = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    res = driver.chat(5, "more detail", history=hist)
    assert seen["url"] == "http://127.0.0.1:8100/chat"
    assert seen["json"]["history"] == hist
    assert res.run_id == 7 and res.final == "ok"


def test_driver_omits_history_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_stream(monkeypatch)
    driver.chat(5, "hi")
    assert "history" not in seen["json"]


# --------------------------------------------------------------------------
# runner --compare
# --------------------------------------------------------------------------

class _Health:
    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def compare_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    cfg = _write(tmp_path, SCENARIO_CFG)
    state: dict[str, Any] = {"next": 100, "calls": [], "deleted": [], "health": []}

    def create_session() -> int:
        state["next"] += 1
        return int(state["next"])

    def chat(session_id: int, prompt: str, **kw: Any) -> driver.ChatResult:
        state["calls"].append({"base": driver.BASE, "session": session_id,
                               "prompt": prompt, "history": kw.get("history")})
        side = "cand" if driver.BASE.endswith(":8100") else "base"
        return driver.ChatResult(run_id=len(state["calls"]), session_id=session_id,
                                 final=f"{side} answer to {prompt}")

    def delete_session(session_id: int, *, base: str | None = None) -> None:
        state["deleted"].append((base, session_id))

    def health(url: str, **_: Any) -> _Health:
        state["health"].append(url)
        return _Health()

    def grounding(db_path: str, run_id: int, **_: Any) -> Grounding:
        state.setdefault("dbs", []).append(db_path)
        return Grounding(run_id=run_id)

    monkeypatch.setattr(driver, "create_session", create_session)
    monkeypatch.setattr(driver, "chat", chat)
    monkeypatch.setattr(driver, "delete_session", delete_session)
    monkeypatch.setattr(runner, "grounding_for", grounding)
    monkeypatch.setattr("benchmark.runner.httpx.get", health)
    out = tmp_path / "out"
    state["out"] = out
    state["argv"] = [
        "runner", "--config", str(cfg), "--out", str(out),
        "--base-url", "http://127.0.0.1:8100", "--db-path", "h4.db",
        "--compare", "http://127.0.0.1:8200", "--compare-db-path", "v241.db",
    ]
    return state


def _results(out: Path) -> list[dict[str, Any]]:
    [p] = [p for p in out.glob("*.json") if not p.name.endswith(".gate.json")]
    data: list[dict[str, Any]] = json.loads(p.read_text(encoding="utf-8"))
    return data


def test_compare_runs_each_case_on_both_instances(
    compare_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", compare_harness["argv"])
    runner.main()
    calls = compare_harness["calls"]
    assert [c["base"] for c in calls] == [
        "http://127.0.0.1:8100", "http://127.0.0.1:8200",
        "http://127.0.0.1:8100", "http://127.0.0.1:8200",
    ]
    # Both instances get the seeded history.
    assert calls[0]["history"] == calls[1]["history"]
    assert len(calls[0]["history"]) == 4
    assert compare_harness["health"] == [
        "http://127.0.0.1:8100/health", "http://127.0.0.1:8200/health",
    ]
    assert compare_harness["dbs"] == ["h4.db", "v241.db", "h4.db", "v241.db"]
    res = _results(compare_harness["out"])
    assert res[0]["scenario"] == 1 and res[0]["suite"] == "scenarios"
    assert res[0]["turns"][0]["user"] == "Can GISC help YOLOSeg?"
    assert res[0]["final"].startswith("cand")
    assert res[0]["compare"]["final"].startswith("base")
    assert res[0]["compare"]["base_url"] == "http://127.0.0.1:8200"
    assert res[1]["kind"] == "chitchat"
    # Each instance's sessions are deleted on that instance.
    assert sorted(compare_harness["deleted"]) == [
        ("http://127.0.0.1:8100", 101), ("http://127.0.0.1:8100", 103),
        ("http://127.0.0.1:8200", 102), ("http://127.0.0.1:8200", 104),
    ]
    gate_files = list(compare_harness["out"].glob("*.gate.json"))
    assert len(gate_files) == 1


def test_compare_requires_its_db_path(
    compare_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = compare_harness["argv"][:-2]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        runner.main()


def test_compare_with_judge_writes_pairwise_both_orders(
    compare_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmark.judge as judge_mod

    async def fake_judge_results(results: list[dict[str, Any]], *, model: str) -> list[dict[str, Any]]:
        for d in results:
            d["judge"] = {"score": 1, "model": model}
        return results

    seen: list[dict[str, str]] = []

    async def fake_pairwise(*, request: str, rubric: str, output_a: str,
                            output_b: str, model: str) -> str:
        seen.append({"request": request, "a": output_a, "b": output_b})
        # Always prefers whichever output is the candidate's.
        return "A" if "cand answer" in output_a else "B"

    monkeypatch.setattr(judge_mod, "judge_results", fake_judge_results)
    monkeypatch.setattr(judge_mod, "load_env", lambda *_: None)
    monkeypatch.setattr(runner, "judge_pairwise", fake_pairwise)
    monkeypatch.setattr(sys, "argv", [*compare_harness["argv"], "--judge"])
    runner.main()
    res = _results(compare_harness["out"])
    # Pairwise only on scenarios v2.41 can attempt (s1-a), not the chitchat case.
    assert res[0]["pairwise"]["orders"] == ["candidate", "candidate"]
    assert res[0]["pairwise"]["verdict"] == "candidate"
    assert "pairwise" not in res[1]
    assert len(seen) == 2
    assert "Can GISC help YOLOSeg?" in seen[0]["request"]
    assert res[0]["compare"]["judge"]["score"] == 1


# --------------------------------------------------------------------------
# gate maths
# --------------------------------------------------------------------------

def _case(cid: str, *, scenario: int | None = None, kind: str | None = None,
          suite: str | None = None, score: int | None = 1, base_score: int | None = 1,
          elapsed: float = 1.0, base_elapsed: float = 1.0,
          orders: list[str] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": cid, "scenario": scenario, "kind": kind, "suite": suite,
        "elapsed_s": elapsed, "judge": {"score": score},
        "compare": {"elapsed_s": base_elapsed, "judge": {"score": base_score}},
    }
    if orders is not None:
        d["pairwise"] = {"orders": orders}
    return d


def _scenarios(passes_per_scenario: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s, n_pass in enumerate(passes_per_scenario, 1):
        for i in range(3):
            out.append(_case(f"s{s}-{i}", scenario=s, suite="scenarios",
                             score=1 if i < n_pass else 0))
    return out


def _by_key(gates: list[gate.Gate]) -> dict[str, gate.Gate]:
    return {g.key: g for g in gates}


def test_gate_a_passes_at_two_of_three_and_twenty_of_twentyfour() -> None:
    # 4 scenarios at 3/3, 4 at 2/3 -> 20/24, every scenario >= 2/3.
    g = gate.gate_a(_scenarios([3, 3, 3, 3, 2, 2, 2, 2]))
    assert g.status == "PASS"
    assert g.numbers["total_pass"] == 20 and g.numbers["total"] == 24


def test_gate_a_fails_when_one_scenario_has_one_of_three() -> None:
    g = gate.gate_a(_scenarios([3, 3, 3, 3, 3, 3, 3, 1]))
    assert g.status == "FAIL"
    assert g.numbers["per_scenario"]["8"] == "1/3"


def test_gate_a_fails_below_twenty_overall() -> None:
    # Every scenario at 2/3 is 16/24 < 20.
    g = gate.gate_a(_scenarios([2] * 8))
    assert g.status == "FAIL"
    assert g.numbers["total_pass"] == 16


def test_gate_a_fails_when_a_scenario_is_missing_or_unjudged() -> None:
    rows = _scenarios([3] * 8)
    assert gate.gate_a(rows[:-3]).status == "FAIL"   # scenario 8 absent
    rows[0]["judge"]["score"] = None
    rows[1]["judge"]["score"] = None
    g = gate.gate_a(rows)
    assert g.status == "FAIL"   # scenario 1: 1/3 (unjudged is not a pass)
    assert g.numbers["unjudged"] == 2


def test_gate_a_not_applicable_without_scenarios() -> None:
    assert gate.gate_a([_case("r", suite="regression")]).status == "N/A"


def test_gate_b_counts_both_orders_and_ties_at_seventy_percent() -> None:
    rows = [
        _case("a", scenario=1, orders=["candidate", "candidate"]),
        _case("b", scenario=2, orders=["tie", "candidate"]),
        _case("c", scenario=3, orders=["baseline", "tie"]),
        _case("d", scenario=6, orders=["baseline", "candidate"]),
        _case("e", scenario=7, orders=["candidate", "candidate"]),
        # scenario 4 is not attemptable by v2.41: ignored even though it loses.
        _case("f", scenario=4, orders=["baseline", "baseline"]),
    ]
    g = gate.gate_b(rows)
    # favourable = 2 + 2 + 1 + 1 + 2 = 8 of 10 = 80 %
    assert g.numbers["favourable"] == 8 and g.numbers["comparisons"] == 10
    assert g.status == "PASS"


def test_gate_b_boundary_is_inclusive_and_fails_below() -> None:
    at_70 = [_case(f"x{i}", scenario=1, orders=o) for i, o in enumerate(
        [["candidate", "tie"]] * 7 + [["baseline", "baseline"]] * 3)]
    # 14 favourable of 20 = 70 %.
    assert gate.gate_b(at_70).status == "PASS"
    below = [_case(f"y{i}", scenario=2, orders=o) for i, o in enumerate(
        [["candidate", "baseline"]] * 3 + [["candidate", "candidate"]])]
    # 3 + 2 = 5 of 8 = 62.5 %.
    g = gate.gate_b(below)
    assert g.status == "FAIL"
    assert g.numbers["ratio"] == pytest.approx(0.625)


def test_gate_b_not_applicable_without_gate_b_cases() -> None:
    assert gate.gate_b([_case("a", scenario=4), _case("r", suite="regression")]).status == "N/A"


def test_gate_b_missing_pairwise_counts_as_two_losses() -> None:
    rows = [_case(f"ok{i}", scenario=1, orders=["candidate", "candidate"]) for i in range(4)]
    rows.append(_case("never-compared", scenario=2))   # no pairwise at all
    g = gate.gate_b(rows)
    # 8 favourable of 10: the missing case keeps its two comparisons, as losses.
    assert g.numbers["comparisons"] == 10 and g.numbers["favourable"] == 8
    assert g.numbers["pairwise_missing"] == 1
    assert "missing 1" in g.detail
    rows.append(_case("gone-too", scenario=3))
    assert gate.gate_b(rows).status == "FAIL"   # 8 / 12 = 66.7 %


def test_gate_b_pairwise_error_counts_as_two_losses() -> None:
    rows = [_case(f"ok{i}", scenario=1, orders=["tie", "candidate"]) for i in range(2)]
    err = _case("judge-threw", scenario=6)
    err["pairwise_error"] = "pairwise judge failed: 503"
    rows.append(err)
    g = gate.gate_b(rows)
    assert (g.numbers["favourable"], g.numbers["comparisons"]) == (4, 6)
    assert g.numbers["pairwise_errors"] == 1 and g.numbers["baseline"] == 2
    assert "errors 1" in g.detail
    assert g.status == "FAIL"


def test_gate_b_reports_both_failed_as_ties() -> None:
    rows = [_case("a", scenario=1, orders=["candidate", "candidate"]),
            _case("b", scenario=2, orders=["tie", "tie"])]
    rows[1]["pairwise"]["how"] = "both-failed"
    g = gate.gate_b(rows)
    assert g.numbers["both_failed"] == 1 and g.numbers["tie"] == 2
    assert g.status == "PASS"
    assert "both-failed 1" in g.detail


def test_gate_c_compares_regression_pass_counts() -> None:
    rows = [
        _case("r1", suite="regression", score=1, base_score=1),
        _case("r2", suite="regression", score=1, base_score=0),
        _case("r3", suite="regression", score=0, base_score=1),
        _case("s", scenario=1, suite="scenarios", score=0, base_score=1),  # not counted
    ]
    g = gate.gate_c(rows)
    assert (g.numbers["candidate_pass"], g.numbers["baseline_pass"]) == (2, 2)
    assert g.status == "PASS"
    rows[1]["judge"]["score"] = 0
    assert gate.gate_c(rows).status == "FAIL"


def test_gate_c_not_applicable_without_regression_cases() -> None:
    assert gate.gate_c([_case("s", scenario=1)]).status == "N/A"


def test_gate_c_fails_when_a_regression_case_was_not_compared() -> None:
    solo = _case("r", suite="regression")
    del solo["compare"]
    g = gate.gate_c([solo, _case("r2", suite="regression")])
    assert g.status == "FAIL" and g.numbers["not_compared"] == 1


@pytest.mark.parametrize("side", ["candidate", "baseline"])
def test_gate_c_fails_on_any_unjudged_verdict(side: str) -> None:
    rows = [_case("r1", suite="regression", score=1, base_score=0),
            _case("r2", suite="regression", score=1, base_score=1)]
    if side == "candidate":
        rows[1]["judge"]["score"] = None
    else:
        rows[1]["compare"]["judge"]["score"] = None   # a judge failure on v2.41
    g = gate.gate_c(rows)
    assert g.status == "FAIL"
    assert "unjudged 1" in g.detail


def test_gate_d_median_latency_ratio() -> None:
    rows = [
        _case("c1", kind="chitchat", elapsed=4.0, base_elapsed=2.0),
        _case("c2", kind="library_count", elapsed=5.0, base_elapsed=3.0),
        _case("c3", kind="chitchat", elapsed=30.0, base_elapsed=2.5),
        _case("qa", scenario=1, elapsed=999.0, base_elapsed=1.0),  # not a simple turn
    ]
    g = gate.gate_d(rows)
    assert g.numbers["candidate_median_s"] == 5.0
    assert g.numbers["baseline_median_s"] == 2.5
    assert g.numbers["ratio"] == pytest.approx(2.0)
    assert g.status == "PASS"   # exactly 2x is allowed
    rows[1]["elapsed_s"] = 5.5
    assert gate.gate_d(rows).status == "FAIL"


def test_gate_d_excludes_errored_rows_and_fails_on_a_candidate_error() -> None:
    rows = [
        _case("c1", kind="chitchat", elapsed=2.0, base_elapsed=2.0),
        _case("c2", kind="chitchat", elapsed=3.0, base_elapsed=2.0),
        _case("c3", kind="library_count", elapsed=0.1, base_elapsed=50.0),
    ]
    rows[2]["compare"]["error"] = "baseline stream dropped"
    g = gate.gate_d(rows)
    # c3 is left out of both medians: 2.5 / 2.0.
    assert g.numbers["cases"] == 2 and g.numbers["errored"] == 1
    assert g.numbers["ratio"] == pytest.approx(1.25)
    assert g.status == "PASS"
    assert "errored 1" in g.detail
    rows[2]["compare"].pop("error")
    rows[2]["error"] = "H4 crashed"
    assert gate.gate_d(rows).status == "FAIL"


def test_gate_d_fails_when_a_simple_case_was_not_compared() -> None:
    rows = [_case("c1", kind="chitchat"), _case("c2", kind="chitchat")]
    del rows[1]["compare"]
    g = gate.gate_d(rows)
    assert g.status == "FAIL" and g.numbers["not_compared"] == 1


def test_gate_d_not_applicable_without_simple_kinds() -> None:
    assert gate.gate_d([_case("qa", scenario=1)]).status == "N/A"


def test_overall_passes_only_when_all_four_pass(tmp_path: Path) -> None:
    scen = _scenarios([3] * 8)
    for d in scen:
        if d["scenario"] in gate.V241_SCENARIOS:
            d["pairwise"] = {"orders": ["candidate", "tie"]}
    reg = [_case("r1", suite="regression"), _case("r2", suite="regression")]
    simple = [_case("c1", kind="chitchat", elapsed=2.0, base_elapsed=2.0)]
    gates = gate.compute_gates(scen + reg + simple)
    assert [g.key for g in gates] == ["G-a", "G-b", "G-c", "G-d"]
    assert gate.overall(gates) == "PASS"
    md = gate.gates_markdown(gates)
    assert "G-a" in md and "PASS" in md
    # A missing gate (N/A) is not a pass.
    assert gate.overall(gate.compute_gates(scen)) != "PASS"


def test_gate_cli_merges_result_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    scen = _scenarios([3] * 8)
    for d in scen:
        if d["scenario"] in gate.V241_SCENARIOS:
            d["pairwise"] = {"orders": ["candidate", "candidate"]}
    reg = [_case("r1", suite="regression"),
           _case("c1", kind="chitchat", suite="regression")]
    a = tmp_path / "scen.json"
    b = tmp_path / "reg.json"
    a.write_text(json.dumps(scen), encoding="utf-8")
    b.write_text(json.dumps(reg), encoding="utf-8")
    out = tmp_path / "gate.json"
    monkeypatch.setattr(sys, "argv", ["gate", "--results", str(a), str(b), "--out", str(out)])
    gate.main()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "PASS"
    assert {g["key"]: g["status"] for g in data["gates"]} == {
        "G-a": "PASS", "G-b": "PASS", "G-c": "PASS", "G-d": "PASS"}
    assert "Overall: PASS" in capsys.readouterr().out


# --------------------------------------------------------------------------
# pairwise paths + --compare restrictions
# --------------------------------------------------------------------------

def _pair_row(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"id": "p", "scenario": 1, "prompt": "q", "final": "cand",
                         "compare": {"final": "base"}}
    d.update(over)
    return d


async def test_pairwise_deterministic_when_one_side_errored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never(**_: Any) -> str:
        raise AssertionError("the judge must not be called")

    monkeypatch.setattr(runner, "judge_pairwise", never)
    a_failed = _pair_row(error="H4 crashed", final="")
    pw = await runner.pairwise_case(a_failed, model="m")
    assert pw["orders"] == ["baseline", "baseline"] and pw["how"] == "deterministic"
    b_failed = _pair_row(compare={"final": "", "error": "v2.41 crashed"})
    pw = await runner.pairwise_case(b_failed, model="m")
    assert pw["orders"] == ["candidate", "candidate"] and pw["how"] == "deterministic"
    both = _pair_row(final="", compare={"final": ""})
    pw = await runner.pairwise_case(both, model="m")
    assert pw["orders"] == ["tie", "tie"] and pw["how"] == "both-failed"


async def test_pairwise_results_records_a_judge_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(**_: Any) -> str:
        raise RuntimeError("503 overloaded")

    monkeypatch.setattr(runner, "judge_pairwise", boom)
    row = _pair_row()
    await runner.pairwise_results([row], model="m")
    assert "pairwise" not in row
    assert "503 overloaded" in row["pairwise_error"]
    g = gate.gate_b([row])
    assert g.numbers["pairwise_errors"] == 1 and g.status == "FAIL"


def test_compare_rejects_cases_that_reuse_a_session(
    tmp_path: Path, compare_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "reuse.toml"
    cfg.write_text('[[cases]]\nid = "deck"\nprompt = "this slide?"\nsession_id = 42\n',
                   encoding="utf-8")
    argv = list(compare_harness["argv"])
    argv[argv.index("--config") + 1] = str(cfg)
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        runner.main()
    assert compare_harness["calls"] == []


@pytest.mark.parametrize("where", ['session_group = "g"', "session_id = 7"])
def test_config_rejects_turns_in_a_shared_session(tmp_path: Path, where: str) -> None:
    text = (f'[[cases]]\nid = "x"\nprompt = "hi"\n{where}\n'
            'turns = [{ user = "u", assistant = "a" }]\n')
    with pytest.raises(ValueError, match="turns"):
        load_config(_write(tmp_path, text))


async def test_pairwise_sees_the_whole_cited_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def judge(*, output_a: str, output_b: str, **_: Any) -> str:
        seen.extend([output_a, output_b])
        return "tie"

    monkeypatch.setattr(runner, "judge_pairwise", judge)
    tail = "the claim's support sits at the end of a long chunk"
    row = _pair_row(cited_chunks=[{"id": 9, "text": "x" * 3000 + tail}])
    await runner.pairwise_case(row, model="m")
    assert any(tail in s for s in seen)
