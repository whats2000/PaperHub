# feedback_review_fork — fork reviewer prompt (Plan H2)

Shipped as `src/paperhub/llm/prompts/feedback_review_fork_v3.yaml` = **v15**, run on
the **flagship** tier (see Round 4).
`feedback_review_fork_v2.yaml` = v11 and `feedback_review_fork_v1.yaml` = v6 are
the previous releases.
`feedback.review.REVIEW_SLOTS` sends a fork-triggered review here and a
keyword-triggered one to `feedback_review/*`. Fork rows stay
`needs_confirmation` whatever the verdict (Ruling K2).

## Hypothesis

Forking at a turn flags its answer, but there is no follow-up message, so
`user_followup` is `""`. The keyword reviewer was never evaluated on that
input. The fork reviewer must:

- return **confirmed** when it can name an error in the answer from the
  question and trace;
- return **uncertain** when the evidence supports the answer;
- never return **rejected**. A fork is not "a follow-up about something else";
- write the `doubt` in the question's language, about the answer (not about
  the fork).

## Setup

- **Query set:** `corpus/feedback_review.fork.jsonl`, 8 fork turns with
  `user_followup = ""`:
  - 5 with an error visible in the trace (`confirmed`), one per reason:
    `wrong_source` (zh, `not read: P2`), `factual` (en), `language_format`
    (ja question, English answer), `wrong_route` (en) and `citation` (zh);
  - 3 whose cited passage supports the answer (`uncertain`): en, zh, ja.

  None of them reuses a prompt example.
- **Sweep model:** `gemini/gemini-3.5-flash-lite` (the deploy small tier), 5 reps.
- **Grade:** verdict == label. The table also shows the reason hit on
  confirmed cases, empty doubts and language mismatches (the doubt's script
  differs from the question's).
- **Pairwise judge:** `gemini/gemini-3.8-flash`, `STAGE_RUBRICS["feedback_review_fork"]`.
  It judges the doubt only, twice with A/B swapped.

## Variants

| ver | change |
|---|---|
| v1 | baseline: `feedback_review/v10`, the keyword prompt every fork used |
| v2 | a dedicated fork prompt: no `<user_followup>`; confirmed or uncertain, never rejected; doubt in the question's language; two paraphrased examples (ja confirmed, zh uncertain) |
| v3 | v2 + one line naming the trace signs of an error (`not read:` on the asked-about paper, a `response_language` the answer ignores, a route that does not fit, a citation about something else) |

## Results (8 turns × 5 reps = 40 per run)

| ver | exp | verdict | reason hit | empty doubt | lang mismatch | tokens in |
|---|---|---|---|---|---|---|
| v1 | 27 | **0/40** (all `rejected`) | 0/25 | 13 | 8 | 935 |
| v2 | 28 | 40/40 | 25/25 | 0 | 4 | 747 |
| v3 | 29 | 40/40 | 25/25 | 0 | 2 | 798 |

Pairwise on the doubt (A wins / B wins / ties):

| A vs B | A | B | tie |
|---|---|---|---|
| v3 vs v1 | 40 | 0 | 0 |
| v3 vs v2 | 14 | 12 | 14 |

## What the outputs show

- **v1** reads an empty follow-up as "not a complaint" and rejects every fork.
  A third of its doubts are empty; the rest say "the follow-up is empty".
- **Fixing it inside the keyword prompt regressed keyword reviews.** Rounds
  `feedback_review/v11`–`v19` added fork rules (and fork examples) to v10.
  The best one (v18) reached 32/40 on forks, but v10 beat it on the keyword
  doubts 37–22. The small tier also read "confirmed" as "the answer is
  confirmed correct" (v15/v16: every supported fork → confirmed). See
  `prompts/feedback_review/README.md`.
- **A separate prompt solves both.** Without the follow-up slot and the
  rejected verdict, v2 and v3 get every verdict right. v3's trace-signal line
  halves the language mismatches (both are ja/zh cases answered in English)
  and edges v2 on the judge.

## Round 2: invented facts (v4–v6)

The live check of v3 (run 783) confirmed a correct answer: "the base
Transformer uses 8 heads" was called wrong because "the paper specifies 16".
Nothing in its inputs said 16. The cited head was figure markup with no head
count, and the model used (wrong) memory of the paper. Replaying run 783's
recorded inputs reproduced it 5/5. The bucket got three cases where the cited
head is on topic but does not show the number (`uncertain`):
`fork-en-markup-citation` (run 783 itself), `fork-en-thin-evidence`,
`fork-zh-thin-evidence`. That makes 11 cases (5 confirmed, 6 uncertain).

| ver | change |
|---|---|
| v4 | v3 + "name it only from what the inputs show …: if the cited text does not show the fact, the answer is not shown wrong" |
| v5 | v3 + "a wrong number, claim or method counts only when the cited text shows the right one" |
| **v6** | v3 + "name it only from what the question, answer and trace show, never from what you know of the paper" |

11 turns × 5 reps = 55 per run:

| ver | exp | verdict | reason hit | miss | tokens in |
|---|---|---|---|---|---|
| v3 | 36 | 50/55 | 25/25 | `fork-en-markup-citation` → confirmed 5/5 | 795 |
| v4 | 37 | 52/55 | 22/25 | `fork-ja-wrong-language` → uncertain 3/5 ("cited text" read as every error) | 831 |
| v5 | 38 | 50/55 | 20/25 | `fork-zh-unread-paper` → uncertain 5/5 | 823 |
| **v6** | 39 | **55/55** | **25/25** | none | 818 |

Pairwise v6 vs v3 (55 pairs): **v6 22**, v3 15, ties 18. Five of v6's wins are
v3's verdict errors on run 783's case; on the judged pairs it is 17–15.

## Why v6

It is the only variant with every verdict and reason right, including the
real failure from run 783. It beats v3 on the judge and costs 818 tokens in,
still less than the keyword prompt (986).

## Known limits

- Language: v6 still writes 4 of 55 doubts in English for a zh/ja question
  (three zh, one ja). The flash-lite tier also drops CJK characters
  now and then, so some "English" doubts are partly garbled CJK.
- The corpus is hand-authored, except `fork-en-markup-citation`. Harvest real fork reviews
  (`feedback:review` steps with `trigger = "fork"`) once they exist.
- Production never sends the user's edited question: the fork endpoint only
  prefills the composer, and the review is scheduled before the user sends
  anything. If the edited text is ever passed in, it needs its own variable
  and cases.

## Round 3: the doubt's language (v7–v12)

v6 wrote some doubts in English for a zh/ja question. The English doubts
copy the examples' English paraphrase almost word for word ("…, so what did
you want to change?"). Three cases joined the bucket, each a zh/ja question
whose answer is supported by English cited text (`uncertain`):
`fork-zh-cn-supported`, `fork-ja-thin-evidence`,
`fork-zh-tw-codeswitch-supported`. That makes 14 cases (5 confirmed, 9
uncertain). Language mismatch = the doubt's script differs from the
question's (kana → ja, Han → zh, else en).

| ver | change |
|---|---|
| v7 | v6, the language line also says an English answer, cited passage or the English examples never make the doubt English |
| v8 | v6 with the examples' doubts written out in the question's language (ja, zh) instead of an English paraphrase |
| v9 | v8 + the uncertain doubt says when the cited text does not show the fact |
| v10 | v8 + a third zh example: an uncertain doubt for a cited passage without the number |
| **v11** | v6 with each example naming its doubt's language: "doubt (written in Japanese) tells the user: …" |
| v12 | v11 + v7-style language line |

Deploy model `gemini/gemini-3.5-flash-lite`, 14 cases:

| ver | exp | runs | verdict | reason hit | lang mismatch | tokens in | misses |
|---|---|---|---|---|---|---|---|
| v6 | 183 + 186 | 140 | 130/140 | 50/50 | 6 | 822 | `fork-ja-thin-evidence` → confirmed 10/10 |
| v7 | 184 | 70 | 61/70 | 21/25 | 1 | 826 | `fork-zh-unread-paper` → uncertain 4/5, `fork-en-markup-citation` → confirmed 5/5 |
| v8 | 185 + 187 | 140 | 133/140 | 48/50 | 0 | 858 | ja-thin 5/10, plus `fork-zh-unread-paper` and `fork-ja-wrong-language` once each |
| v9 | 188 | 70 | 56/70 | 17/25 | 0 | 870 | unread-paper 5/5, wrong-language 3/5, ja-thin 5/5 |
| v10 | 189 | 70 | 60/70 | 20/25 | 0 | 984 | unread-paper 3/5, wrong-language 2/5, ja-thin 5/5 |
| **v11** | 190 | 140 | **139/140** | **50/50** | **0** | 832 | ja-thin 1/10 |
| v12 | 191 | 140 | 130/140 | 50/50 | 2 | 836 | ja-thin 10/10 |

v8 also copies its uncertain example's wording: on run 783's case it wrote
"the cited text and the answer both state 8 heads" 4/5 times, though the cited
text is figure markup. Pairwise v8 vs v6: 27–24, ties 19, and v6 won that case
4–1.

Pairwise v11 vs v6 (reps 0–4 of each, 70 pairs, `gemini/gemini-3.8-flash`,
A/B swapped): **v11 26**, v6 24, ties 20. Five pairs went to v11 on the
verdict (`fork-ja-thin-evidence`); on the doubts alone it is 21–24. Most of
v6's doubt wins are zh cases where v11's sentence has garbled characters
(`怨問`, `叀考`), the flash-lite artefact noted below.

## Why v11

It is the only variant with no language mismatch and no verdict regression:
the original 11 cases 110/110, every labelled reason, and the best verdicts on
the new cases. It costs 10 tokens more than v6. The doubts are on par with
v6 on the judge.

## Known limits (round 3)

- The flash-lite tier drops or swaps CJK characters in some zh doubts in
  every variant; the judge penalises these, and they are not a language
  mismatch.
- `fork-ja-thin-evidence` is labelled `uncertain` like the other thin-evidence
  cases (the cited head is cut before the number). One could also read it as a
  `citation` error; v6 always does.

## Round 4: full cited passages and the flagship tier (v13–v15)

**Full passages.** Live fork reviews 825 and 827 confirmed two correct answers
as `citation` errors. The trace summary held only the first 200 characters of
the cited chunk ("This section describes the training regime … Training Data
and Batching …"), and the fact the answer used (`P_drop = 0.1`, `100,000
steps`) came later in the chunk. `load_review_inputs` now shows every cited
chunk in full up to `CITED_TEXT_BUDGET` (16,000 characters in all). It cuts the
chunk that crosses the budget with `...` and lists later chunks without their
text. The step records `cited_passages`. Four cases joined the bucket (18 in
all, 7 confirmed / 11 uncertain): runs 825 and 827 rebuilt with the full passage
(`uncertain`), and two wrong answers under the same long passage (`confirmed
factual`) as a guard. With the shipped v11 on flash-lite (exp 211), runs 825 and
827 now come out `uncertain` 5/5.

**Garbled CJK.** The small tier swaps CJK characters for nonsense ones with a
nearby codepoint (詢問 → 怨問, 參考 → 叀考, 遮罩 → 镮罩), and sometimes writes a
whole run of them. A flagship proofreading pass flagged these on the zh/ja cases
of both buckets (5 reps), first on the deploy call path
(`LiteLlmAdapter.structured`, provider-default temperature) with the shipped
prompts:

| model | garbled zh/ja doubts | latency | ≈ $ / review |
|---|---|---|---|
| `gemini-3.5-flash-lite`, deploy path | **17/135 (12.6 %)** | 1.0 s | 0.001 |
| `gemini-3.5-flash-lite`, eval path (temperature 0) | 13/135 (9.6 %) | 1.0 s | 0.001 |
| `gemini-3.8-flash`, deploy path | **1/135 (0.7 %)** | 5.1 s | 0.002 |
| `gemini-pro-latest` (this workspace's flagship setting), sweeps below | 0/135 | 3.5–7.6 s live | ≈ 0.01 |

Temperature is not the cause. A local check catches only 15 of the 30 garbled
flash-lite doubts: it looks for a CJK character outside Big5, GB2312 and
Shift-JIS, a stray halfwidth form or an odd CJK symbol. Detect-and-retry would
therefore still leave about 6 %, while the flagship tier writes almost none. A
review is a background job that runs only after a correction or a fork, so a
few seconds and about a cent per review are acceptable.
`PAPERHUB_FEEDBACK_REVIEW_MODEL` now defaults to the flagship tier.

**v11 on the flagship** gets every verdict right except the two thin-evidence
forks. It reads a passage cut before the number as a citation that "does not
support" the answer. A passage past the budget ends in `...`, so production can
show it the same input.

| ver | change |
|---|---|
| v13 | v11 + "A cited passage that ends in `...` was cut short; a fact it does not show may be past the cut, so its absence is not an error." |
| v14 | v11 with the citation sign narrowed: "a cited passage about something else (not one on the question's topic that is cut with `...` before the fact)" |
| **v15** | v13 + the confirmed list's citation item reads "a citation that does not support its sentence — not one cut with `...` before the fact" |

18 turns × 5 reps = 90 per run:

| ver | model | exp | verdict | reason hit | lang mismatch | garbled zh/ja | tokens in | misses |
|---|---|---|---|---|---|---|---|---|
| v11 | flash-lite | 211 | 89/90 | 35/35 | 0 | 6/60 | 973 | ja-thin → confirmed 1/5 |
| v11 | 3.8-flash | 213 | 80/90 | 35/35 | 0 | 0/60 | 973 | zh-thin, ja-thin → confirmed 5/5 each |
| v11 | pro-latest | 267 | 80/90 | 35/35 | 0 | 0/60 | 973 | zh-thin, ja-thin → confirmed 5/5 each |
| v13 | 3.8-flash | 218 | 90/90 | 35/35 | 0 | 0/60 | 1006 | none |
| v13 | pro-latest | 268 | 84/90 | 35/35 | 0 | 0/60 | 1006 | ja-thin 5/5, zh-thin 1/5 |
| v14 | 3.8-flash | 220 | 88/90 | 35/35 | 0 | 0/60 | 991 | ja-thin 2/5 |
| **v15** | **pro-latest** | 270 | **90/90** | **35/35** | 0 | **0/60** | 1017 | none |
| **v15** | **3.8-flash** | 271 | **90/90** | **35/35** | 0 | **0/60** | 1017 | none |
| v13 | flash-lite | 263 | 83/90 | 34/35 | 1 | 9/60 | 1006 | ja-thin 5/5, ja-wrong-language, markup-citation |
| v15 | flash-lite | 272 | 79/90 | 28/35 | 1 | 8/60 | 1017 | unread-paper 4/5, ja-wrong-language 3/5, ja-thin 3/5, markup-citation 1/5 |

Pairwise on the doubt (`gemini/gemini-3.8-flash`, A/B swapped; "verdict" =
pairs decided by a wrong verdict):

| A vs B | A | B | tie | verdict |
|---|---|---|---|---|
| v13 3.8-flash (218) vs v11 3.8-flash (213) | **33** | 11 | 46 | 10–0 |
| v13 3.8-flash (218) vs v11 flash-lite (211, the old deployment) | **58** | 9 | 23 | 1–0 |
| v15 pro-latest (270) vs v11 flash-lite (211) | **43** | 19 | 28 | 1–0 |
| v15 pro-latest (270) vs v13 pro-latest (268) | **22** | 15 | 53 | 6–0 |

**Why v15 on the flagship tier:** it is the only configuration with every
verdict right and no garbled doubt on both flagship models: `gemini-3.8-flash`
(the project's default flagship) and `gemini-pro-latest` (this workspace's
runtime flagship setting). It also beats the old deployment on the judge.
`REVIEW_MAX_TOKENS` went from 1024 to 4096: Gemini counts thinking tokens
against the cap, the 3.8-flash reviewer thinks for 140–960 tokens on these
inputs, and 1024 truncated 2 of 135 verdicts. The eval stage now replays under
the same cap.

## Known limits (round 4)

- **The small tier is worse with v15 than with v11** (79/90 vs 89/90). It is
  only used when the flagship call fails (the adapter's fallback) or when an
  operator pins `PAPERHUB_FEEDBACK_REVIEW_MODEL` to it, and a fork row always
  waits for the user's confirmation. Keep the flagship tier for this stage.
- The thin-evidence cases (`...`-cut passages) predate the full-passage change.
  Production now cuts only at the 16,000-character budget, so they stand for
  that budget cut.
