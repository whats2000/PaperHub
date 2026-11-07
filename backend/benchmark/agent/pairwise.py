"""Pairwise head-to-head of two recorded experiments (SRS §III-9).

For stages whose quality is not a single deterministic label (e.g. translation
fidelity/fluency), compare two variants case-by-case: the deterministic check
decides first (a contract failure always loses); when both pass, an LLM judge
picks the better output. The judge runs twice with A/B swapped and a win only
counts when both orders agree — otherwise it is a tie (cancels position bias).
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from benchmark.agent.corpus import CorpusCase
from benchmark.agent.grade import judge_pairwise
from benchmark.agent.stages import StageSpec

PairwiseFn = Callable[..., Awaitable[str]]

STAGE_RUBRICS: dict[str, str] = {
    "router": (
        "Both outputs already route the message to the correct intent; judge the "
        "rest. Better = `resolved_query` is a faithful, self-contained rewrite of "
        "the user's message (same request and scope, nothing added or dropped, in "
        "the message's language) and `response_language` names that language. "
        "Ignore `confidence`, `reasoning`, `also_requested` and `llm_intent`."
    ),
    "translate_blocks": (
        "Judge translation quality for each block. Fidelity first: every claim, "
        "number, hedge and negation of the source is carried over, nothing added "
        "or dropped. Then fluency: natural academic prose in the target language "
        "(and its regional variety). Placeholders such as ⟦0⟧ / ⟦BR⟧ must sit in a "
        "grammatically sensible position. Terms in <terminology> must be used as "
        "specified. Identifiers, model names and variable names stay untranslated."
    ),
    "feedback_review": (
        "Both outputs already have the correct verdict; judge ONLY the `doubt` "
        "sentence, which is shown to the user as the reviewer's concern. Better = "
        "names the concrete problem (or, for a rejection, why the follow-up is not "
        "a complaint; for uncertain, what is unclear and what to ask) using facts "
        "from the request's question, answer and trace_summary; invents nothing; "
        "is exactly one sentence; is written in the language of user_followup. "
        "Penalise a vague doubt that would fit any case. Ignore `reasons`."
    ),
    "feedback_review_fork": (
        "The user forked the chat at the answer under review, without writing why. "
        "Both outputs already have the correct verdict; judge ONLY the `doubt` "
        "sentence, which is shown to the user. Better = names the concrete error "
        "(for uncertain: what the evidence shows, then asks what they wanted to "
        "change) using facts from the request's question, answer and "
        "trace_summary; invents nothing; is exactly one sentence; is written in "
        "the language of question. Penalise a vague doubt that would fit any "
        "case, or one that talks about the fork instead of the answer. Ignore "
        "`reasons`."
    ),
    "ideation_card": (
        "Both cards are mechanism cards for the document in the request (title, "
        "abstract, per-section digest). Judge, in order: (1) Faithfulness — every "
        "claim in problem, mechanism, assumptions and limitations is supported by "
        "the digest or abstract; nothing invented, no wrong mechanism. (2) "
        "Domain-free phrasing — the problem and mechanism are stated in generic "
        "terms (data shape, objective, operation) with no field nouns, so an "
        "engineer from another field would recognise the same problem in theirs; "
        "jargon such as 'image', 'word', 'robot', 'token', 'protein' or the paper's "
        "own method name in those two fields is a defect. The mechanism must be the "
        "paper's core reusable idea, concrete enough to transplant, not a summary "
        "of results. (3) Citations — each field cites the section(s) that actually "
        "support it. (4) Concision. Domains are short field names; ignore their order."
    ),
    "ideation": (
        "Both outputs are ranked idea lists for the request (a problem, or free "
        "exploration when mode is 'explore', plus the library's mechanism cards). "
        "Judge, in order: (1) Fit — the top ideas' source mechanisms (as stated on "
        "the cited card) actually remove the problem's bottleneck (in explore mode, "
        "the target card's problem); an analogy that only shares vocabulary is a "
        "defect. (2) Cross-domain value — a mechanism borrowed from another field "
        "that transplants concretely beats an obvious same-field suggestion or a "
        "repeat of what the user already tried. (3) Grounding — each idea's cited "
        "section is the card section that holds the borrowed mechanism, and the "
        "rationale says what the mechanism does in the new setting without "
        "inventing claims the card does not make. (4) Ranking and variety — the best "
        "idea is first, and the list is not the same idea repeated. (5) Each "
        "rationale names the borrowed mechanism and the paper it comes from, and "
        "titles and rationales are in the response_language (when empty, the "
        "language the problem is written in). (6) When no card's mechanism removes "
        "a concrete bottleneck, no ideas plus a short no_fit_reason beats "
        "confident filler."
    ),
}


@dataclass
class PairRow:
    case_id: str
    rep: int
    winner: str  # "A" | "B" | "tie"
    how: str  # "deterministic" | "judge" | "judge-split" | "both-failed" | "missing"


@dataclass
class PairwiseReport:
    label_a: str
    label_b: str
    rows: list[PairRow] = field(default_factory=list)

    def count(self, who: str) -> int:
        return sum(1 for r in self.rows if r.winner == who)

    def to_markdown(self) -> str:
        lines = [
            f"# Pairwise: A = {self.label_a} vs B = {self.label_b}", "",
            f"- A wins: {self.count('A')} · B wins: {self.count('B')} · ties: {self.count('tie')}",
            "", "| case | rep | winner | decided by |", "|---|---|---|---|",
        ]
        lines += [f"| {r.case_id} | {r.rep} | {r.winner} | {r.how} |" for r in self.rows]
        return "\n".join(lines)


def _request_text(case: CorpusCase) -> str:
    return json.dumps(case.variables, ensure_ascii=False, indent=1)


def _output(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None or row.get("error"):
        return None
    out = json.loads(row.get("output_json") or "{}")
    return out if isinstance(out, dict) else None


async def pairwise_compare(
    spec: StageSpec, cases: list[CorpusCase],
    scores_a: list[dict[str, Any]], scores_b: list[dict[str, Any]], *,
    label_a: str, label_b: str, judge_model: str, judge: PairwiseFn | None = None,
) -> PairwiseReport:
    fn = judge or judge_pairwise
    rubric_base = STAGE_RUBRICS.get(spec.key, "")
    a_by = {(s["case_id"], int(s["rep"])): s for s in scores_a}
    b_by = {(s["case_id"], int(s["rep"])): s for s in scores_b}
    report = PairwiseReport(label_a=label_a, label_b=label_b)
    for case in cases:
        reps = sorted({r for (cid, r) in a_by if cid == case.case_id}
                      | {r for (cid, r) in b_by if cid == case.case_id})
        for rep in reps:
            out_a = _output(a_by.get((case.case_id, rep)))
            out_b = _output(b_by.get((case.case_id, rep)))
            ok_a = out_a is not None and spec.deterministic_score(case.expect, out_a) != 0.0
            ok_b = out_b is not None and spec.deterministic_score(case.expect, out_b) != 0.0
            if ok_a != ok_b:
                report.rows.append(PairRow(case.case_id, rep, "A" if ok_a else "B", "deterministic"))
                continue
            if not ok_a:
                report.rows.append(PairRow(case.case_id, rep, "tie", "both-failed"))
                continue
            assert out_a is not None and out_b is not None
            rubric = "\n".join(x for x in (rubric_base, case.rubric) if x)
            req = _request_text(case)
            ta = json.dumps(out_a, ensure_ascii=False, indent=1)
            tb = json.dumps(out_b, ensure_ascii=False, indent=1)
            first = await fn(request=req, rubric=rubric, output_a=ta, output_b=tb, model=judge_model)
            second = await fn(request=req, rubric=rubric, output_a=tb, output_b=ta, model=judge_model)
            swapped = {"A": "B", "B": "A"}.get(second, "tie")
            if first == swapped:
                report.rows.append(PairRow(case.case_id, rep, first, "judge"))
            else:
                report.rows.append(PairRow(case.case_id, rep, "tie", "judge-split"))
    return report
