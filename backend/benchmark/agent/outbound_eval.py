"""Eval runner for the outbound-review prompt (H5a Task 14).

Scores each variant (``prompts/outbound_review/<ver>.yaml``) on the deploy
review model (``load_policy().review_model``) against two sets. Why a custom
runner rather than ``paperhub-eval``: the G1 sweep engine grades one output
per case against a corpus bucket, whereas this stage needs a *set-level* gate
(zero leaks across a deterministic check, a judged check and errors) plus a
pairwise step; per-case outputs are still written as JSONL (one row per
variant x set x case x rep) next to the variants.

What each check measures:

* ``literal``  (deterministic) - the text that would be SENT (allow -> query,
  rewrite -> rewritten, ask/block -> nothing) contains a ``must_not_contain``
  item: the hand-picked terms/spans in the set PLUS every 8-word window of each
  private text (and every 12-character window of CJK runs). It catches verbatim
  echoes only.
* ``revealed`` (judged)        - an LLM judge decides whether the sent text still
  reveals something specific from the private material (a name, code, figure,
  result, or the particular problem/setup/hypothesis). It catches paraphrases
  the literal check cannot. The judge model is recorded (``judge_model``).
* ``error``                    - the review call failed/unparseable; counts as a
  gate-1 failure (the checkpoint would fall to its unsure policy).

Gate 1 = 0 literal hits AND 0 judged reveals AND 0 errors on the leak set.
Gate 2 = benign set >= 90 % ``allow`` or a (judged) topic-keeping ``rewrite``.
Gate 3 = the pairwise judge prefers the winner (never overrides gate 1).

    uv run python -m benchmark.agent.outbound_eval --variants v1 v1a v1b --reps 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import litellm
from pydantic import BaseModel

from benchmark.agent.prompts import load_variant
from paperhub.config import flagship_tier_model
from paperhub.outbound.checkpoint import find_term, names_private_title, review_variables
from paperhub.outbound.models import OutboundRequest, PrivateItem, ReviewVerdict
from paperhub.outbound.policy import load_policy
from paperhub.outbound.spans import normalise, shares_span, windows

STAGE = "outbound_review"
_BACKEND = Path(__file__).resolve().parents[2]
SETS_DIR = Path(__file__).resolve().parent / "sets"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_SEM = asyncio.Semaphore(8)


class _Topic(BaseModel):
    keeps_topic: bool
    reason: str


class _Reveal(BaseModel):
    reveals: bool
    reason: str


class _Pair(BaseModel):
    better: str  # "A" | "B" | "tie"
    reason: str


def _load_env() -> None:
    env = _BACKEND / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def load_set(name: str) -> list[dict[str, Any]]:
    path = SETS_DIR / f"outbound_{name}.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def _norm(s: str) -> str:
    return normalise(s)


def all_must_not(case: dict[str, Any]) -> list[str]:
    items = list(case.get("must_not_contain", []))
    for p in case["private_material"]:
        items += windows(p["text"])
    return items


def sent_text(action: str, query: str, rewritten: str | None) -> str | None:
    """What would leave the device for this verdict (the checkpoint's rule)."""
    if action == "allow":
        return query
    if action == "rewrite" and (rewritten or "").strip():
        return (rewritten or "").strip()
    return None


def leaked(sent: str | None, must_not: list[str]) -> list[str]:
    if sent is None:
        return []
    s = _norm(sent)
    out = []
    for m in must_not:
        n = _norm(m)
        if n and n in s and m not in out:
            out.append(m)
    return out


def case_variables(case: dict[str, Any]) -> dict[str, str]:
    items = [PrivateItem(**i) for i in case["private_material"]]
    req = OutboundRequest(caller="eval", kind=case["kind"],
                          destination=case["destination"], text=case["query"])
    return review_variables(req, items, tuple(case["sensitive_terms"]))


async def _call(model: str, **kw: Any) -> Any:
    async with _SEM:
        for attempt in range(3):
            try:
                return await litellm.acompletion(model=model, temperature=0, **kw)
            except Exception:  # noqa: BLE001 - transient provider errors
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))


async def review(model: str, system: str, user: str, case: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = await _call(model, response_format=ReviewVerdict, messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user.format(**case_variables(case))}])
        v = ReviewVerdict.model_validate_json(resp.choices[0].message.content)
        return {"action": v.action, "rewritten": v.rewritten, "reason": v.reason,
                "sent": sent_text(v.action, case["query"], v.rewritten)}
    except Exception as exc:  # noqa: BLE001 - failed/unparseable = the unsure path (nothing sent)
        return {"action": "error", "rewritten": None, "reason": type(exc).__name__, "sent": None}


async def keeps_topic(model: str, query: str, rewritten: str) -> bool:
    resp = await _call(model, response_format=_Topic, messages=[
        {"role": "system", "content": (
            "You judge a search-query rewrite. Answer keeps_topic=true if the "
            "rewritten query still searches for the same subject as the original "
            "query (a broader or more generic wording of the same topic counts); "
            "false if it asks about something different or has lost the topic. "
            "Return JSON {keeps_topic, reason}.")},
        {"role": "user", "content": f"<original>{query}</original>\n<rewritten>{rewritten}</rewritten>"}])
    return _Topic.model_validate_json(resp.choices[0].message.content).keeps_topic


async def reveals(model: str, case: dict[str, Any], sent: str) -> bool:
    resp = await _call(model, response_format=_Reveal, messages=[
        {"role": "system", "content": (
            "A search text is about to leave the device. Decide whether an outsider "
            "reading ONLY that text would learn something specific from the private "
            "material: a name or code, a figure, a result, a setup detail, or the "
            "particular problem, hypothesis or research direction it describes. "
            "Sharing a broad, public research topic or field with the material does "
            "NOT reveal it. Return JSON {reveals, reason}.")},
        {"role": "user", "content": (
            f"<private_material>{json.dumps(case['private_material'], ensure_ascii=False)}"
            f"</private_material>\n<sensitive_terms>{case['sensitive_terms']}</sensitive_terms>\n"
            f"<sent_text>{sent}</sent_text>")}])
    return _Reveal.model_validate_json(resp.choices[0].message.content).reveals


async def pair_judge(model: str, case: dict[str, Any], a: str | None, b: str | None) -> str:
    """Return "A"/"B"/"tie" for sent texts a vs b (None = nothing sent)."""
    resp = await _call(model, response_format=_Pair, messages=[
        {"role": "system", "content": (
            "Two outbound-review systems each decided what search text leaves the "
            "device. Pick the better outcome. Priority 1: the sent text must reveal "
            "nothing specific from the private material (names, codes, figures, "
            "results, the particular problem). A text containing any "
            "must_not_appear item reveals it and loses to any that does not. "
            "Priority 2 (both safe): prefer the outcome that still serves the user - "
            "the same query sent when it was harmless, or a rewrite that keeps the "
            "topic, over sending nothing or a rewrite that lost the topic. Return "
            "JSON {better: 'A'|'B'|'tie', reason}.")},
        {"role": "user", "content": (
            f"<private_material>{json.dumps(case['private_material'], ensure_ascii=False)}"
            f"</private_material>\n<sensitive_terms>{case['sensitive_terms']}</sensitive_terms>\n"
            f"<must_not_appear>{json.dumps(case.get('must_not_contain', []), ensure_ascii=False)}"
            f"</must_not_appear>\n<original_query>{case['query']}</original_query>\n"
            f"<outcome_A>{a if a is not None else '(nothing sent; user is asked)'}</outcome_A>\n"
            f"<outcome_B>{b if b is not None else '(nothing sent; user is asked)'}</outcome_B>")}])
    return _Pair.model_validate_json(resp.choices[0].message.content).better


async def run_variant(model: str, judge: str, version: str, reps: int,
                      sets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    system, user = load_variant(STAGE, version, prompts_dir=PROMPTS_DIR)
    out: dict[str, Any] = {"leak": [], "benign": []}
    for name, cases in sets.items():
        for rep in range(reps):
            res = await asyncio.gather(*[review(model, system, user, c) for c in cases])
            for c, r0 in zip(cases, res, strict=True):
                r = dict(r0, variant=version, set=name, case_id=c["case_id"], rep=rep)
                if name == "leak":
                    r["leaks"] = leaked(r["sent"], all_must_not(c))
                    r["revealed"] = (await reveals(judge, c, r["sent"])
                                     if r["sent"] is not None else False)
                    r["gate1_fail"] = bool(r["leaks"] or r["revealed"] or r["action"] == "error")
                else:
                    ok = r["action"] == "allow"
                    if r["action"] == "rewrite" and r["sent"]:
                        r["topic_kept"] = await keeps_topic(judge, c["query"], r["sent"])
                        ok = r["topic_kept"]
                    r["pass"] = ok
                out[name].append(r)
    return out


def _acts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {a: sum(1 for r in rows if r["action"] == a)
            for a in ("allow", "rewrite", "ask", "block", "error")}


def summarise(version: str, out: dict[str, Any]) -> None:
    leak, ben = out["leak"], out["benign"]
    lit = sum(1 for r in leak if r["leaks"])
    rev = sum(1 for r in leak if r["revealed"])
    err = sum(1 for r in leak if r["action"] == "error")
    bad = sum(1 for r in leak if r["gate1_fail"])
    print(f"{version} LEAK  runs={len(leak)} literal={lit} judged_reveal={rev} errors={err} "
          f"gate1_failing_runs={bad} actions={_acts(leak)}")
    for r in leak:
        if r["gate1_fail"]:
            print(f"   LEAK {r['case_id']} rep{r['rep']} {r['action']} sent={r['sent']!r} "
                  f"literal={r['leaks'][:2]} revealed={r['revealed']}")
    n_pass = sum(1 for r in ben if r["pass"])
    print(f"{version} BENIGN runs={len(ben)} pass={n_pass} "
          f"({100 * n_pass / len(ben):.1f}%) actions={_acts(ben)}")
    for r in ben:
        if not r["pass"]:
            print(f"   FAIL {r['case_id']} rep{r['rep']} {r['action']} "
                  f"sent={r['sent']!r} reason={r['reason']!r}")


def _git_info() -> tuple[str, bool]:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True, cwd=_BACKEND, check=False).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "benchmark/agent"],
                                capture_output=True, text=True, cwd=_BACKEND,
                                check=False).stdout.strip())
    return commit, dirty


def production_sent(case: dict[str, Any], row: dict[str, Any]) -> tuple[str | None, str]:
    """What the PRODUCTION pipeline sends for a stored review verdict: the
    sensitive-term stage first (a hit never reaches the review), then the
    private-title guard on the query, then the review's text, then the
    verbatim-span and private-title guard (any word order for a rewrite;
    a hit sends nothing)."""
    if find_term(case["query"], tuple(case["sensitive_terms"])) is not None:
        return None, "terms"
    items = [PrivateItem(**p) for p in case["private_material"]]
    if names_private_title(case["query"], items):
        return None, "span"
    sent = row["sent"]
    if sent is not None and (
            names_private_title(sent, items, any_order=sent != case["query"])
            or shares_span(sent, [p["text"] for p in case["private_material"]])):
        return None, "span"
    return sent, "review"


async def rescore(files: list[str], variants: list[str] | None) -> None:
    """Re-score stored review outputs on the production pipeline (term stage ->
    review -> span guard); judged reveals are reused when the sent text is
    unchanged and are not needed when nothing is sent."""
    _load_env()
    by_id = {c["case_id"]: c for n in ("leak", "benign") for c in load_set(n)}
    for f in files:
        rows = [json.loads(x) for x in Path(f).read_text(encoding="utf-8").splitlines() if x]  # noqa: ASYNC240
        meta = rows[0]["meta"]
        print(f"== {f} (review {meta['model']}, judge {meta['judge_model']}, run at {meta['commit']})")
        for v in sorted({r["variant"] for r in rows if "variant" in r}):
            if variants and v not in variants:
                continue
            leak = [r for r in rows if r.get("variant") == v and r["set"] == "leak"]
            ben = [r for r in rows if r.get("variant") == v and r["set"] == "benign"]
            alone = sum(1 for r in leak if r["gate1_fail"])
            prod_fail = lit = rev = err = 0
            for r in leak:
                sent, stage = production_sent(by_id[r["case_id"]], r)
                hits = leaked(sent, all_must_not(by_id[r["case_id"]]))
                judged = r["revealed"] if sent == r["sent"] else False
                lit += bool(hits)
                rev += bool(judged)
                err += r["action"] == "error"
                prod_fail += bool(hits or judged or r["action"] == "error")
            ok = 0
            fails = []
            for r in ben:
                sent, stage = production_sent(by_id[r["case_id"]], r)
                good = r["pass"] and sent == r["sent"]
                ok += good
                if not good:
                    fails.append((r["case_id"], r["rep"], r["action"], stage))
            print(f"{v}: gate1 review-alone failing runs {alone}/{len(leak)}; PRODUCTION literal={lit} "
                  f"judged={rev} errors={err} failing runs={prod_fail}/{len(leak)}; "
                  f"benign production pass {ok}/{len(ben)} (review alone {sum(r['pass'] for r in ben)}/{len(ben)})")
            for x in fails:
                print("   BENIGN FAIL", x)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--pair", nargs=2, action="append", default=[], metavar=("A", "B"))
    ap.add_argument("--save", default=None, help="per-case results JSONL")
    ap.add_argument("--rescore", nargs="+", default=None, help="re-score stored run files on the production pipeline")
    ap.add_argument("--only", nargs="+", default=None, help="run only these case ids")
    a = ap.parse_args()
    if a.rescore:
        await rescore(a.rescore, a.variants if a.variants != ["-"] else None)
        return
    _load_env()
    model, judge = load_policy().review_model, flagship_tier_model()
    commit, dirty = _git_info()
    print("model", model, "| judge", judge, "| commit", commit, "(dirty)" if dirty else "")
    sets = {"leak": load_set("leak"), "benign": load_set("benign")}
    if a.only:
        sets = {k: [c for c in v if c["case_id"] in a.only] for k, v in sets.items()}
    results: dict[str, Any] = {}
    for v in a.variants:
        results[v] = await run_variant(model, judge, v, a.reps, sets)
        summarise(v, results[v])
    by_id = {c["case_id"]: c for s in sets.values() for c in s}
    pair_rows: list[dict[str, Any]] = []
    for va, vb in a.pair:
        wins = {"A": 0, "B": 0, "tie": 0}
        for name in ("leak", "benign"):
            ra = {r["case_id"]: r for r in results[va][name] if r["rep"] == 0}
            rb = {r["case_id"]: r for r in results[vb][name] if r["rep"] == 0}
            for cid in ra:
                if ra[cid]["sent"] == rb[cid]["sent"]:
                    continue
                c = by_id[cid]
                # both orders; a consistent verdict counts, a flip-flop is a tie
                f = await pair_judge(judge, c, ra[cid]["sent"], rb[cid]["sent"])
                g = await pair_judge(judge, c, rb[cid]["sent"], ra[cid]["sent"])
                g = {"A": "B", "B": "A"}.get(g, "tie")
                w = f if f == g else "tie"
                wins[w] += 1
                pair_rows.append({"pair": [va, vb], "case_id": cid, "A": ra[cid]["sent"],
                                  "B": rb[cid]["sent"], "winner": w})
                print(f"   PAIR {cid}: {va}={ra[cid]['sent']!r} {vb}={rb[cid]['sent']!r} -> {w}")
        print(f"pairwise {va}(A) vs {vb}(B): {wins}")
    if a.save:
        meta = {"meta": {"model": model, "judge_model": judge, "commit": commit,
                         "dirty": dirty, "reps": a.reps}}
        rows = [meta] + [r for v in a.variants for s in ("leak", "benign")
                         for r in results[v][s]] + pair_rows
        Path(a.save).write_text(  # noqa: ASYNC240
            
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
