# feedback_review — keyword-flag reviewer prompt (Plan H2)

Shipped as `src/paperhub/llm/prompts/feedback_review_v3.yaml` = **v21**, run on
the **flagship** tier since Round 4
(`feedback_review_v2.yaml` = v20 and `feedback_review_v1.yaml` = v10, the
previous releases). Fork-triggered
reviews use their own prompt: see `../feedback_review_fork/README.md`.

## Hypothesis

A correction keyword in the user's next message ("wrong", "不對", "違う", …)
only flags the previous answer. The reviewer must tell three cases apart:

- **confirmed**: a real correction it can name;
- **rejected**: the keyword is incidental ("is it wrong to use CEM here?",
  "我是問題的提出者…");
- **uncertain**: a vague or hedged complaint it cannot verify.

It must also pick the matching reason ids and write one concrete `doubt`
sentence in the follow-up's language, because the user sees that sentence.

## Setup

- **Query set:** `corpus/feedback_review.core.jsonl`, 16 labelled turns:
  - 6 corrections (zh/en/ja), one per reason: `wrong_source` ("我是問第二篇論文"),
    `off_topic` ("wrong, I asked about the decoder"), `factual`,
    `language_format`, `wrong_route` and `citation`;
  - 5 non-corrections that contain a keyword: `rejected`;
  - 5 ambiguous turns, where the trace supports the answer and the complaint
    is bare, hedged or underspecified: `uncertain`.

  Each case has a realistic `trace_summary` (route, sections read, cited chunk
  text) and the `{reasons}` block from `feedback.models.format_reasons_block()`.
  A test checks that every follow-up fires the default keyword trigger.
- **Sweep model:** `gemini/gemini-3.5-flash-lite`, the deploy small tier
  (`PAPERHUB_MODEL_SMALL`), 3 reps per variant.
- **Deterministic grade:** verdict == label. The table also shows three checks
  read from the outputs:
  - **reason hit**: a confirmed case includes its labelled reason;
  - **empty doubt**;
  - **language mismatch**: the doubt's script (kana / CJK / Latin) differs from
    the follow-up's.
- **Pairwise judge** (`cli pairwise`, `STAGE_RUBRICS["feedback_review"]`):
  `gemini/gemini-3.8-flash` (flagship tier) judges only the `doubt` sentence
  (specific, grounded in the inputs, one sentence, in the follow-up's
  language). It runs twice with A/B swapped, and a win counts only when both
  orders agree. A verdict error loses deterministically.

## Variants

| ver | change |
|---|---|
| v1 | Verdict definitions + output rules, no examples (baseline) |
| v2 | v1 + one example per verdict (DDPM en / ResNet ja / batch-size en) |
| v3 | Numbered 3-step decision procedure + v2's examples |
| v4 | v3 + an explicit doubt-language rule and a third-person voice |
| v5 | v4 with the example languages rotated (confirmed ja, rejected en, uncertain zh) |
| v6 | v3, but the examples *describe* the doubt ("doubt says: …") instead of writing it in a language |
| v7 | v1 + "a bare or hedged complaint is uncertain" — **disqualified**: its illustration quotes the corpus follow-up "不對吧" verbatim |
| v8 | v6 + "address the user as *you*, start with a capital" |
| v9 | v6 + a grounded, second-person doubt ("cite the number / section / paper at issue; for uncertain, ask what they think is wrong"); examples still paraphrase |
| v10 | v9 + "the doubt follows the follow-up's *main* language; English paper names inside it do not make it English" |
| v11–v19 | fork rounds: v10 + rules (and v12/v14/v16/v18/v19 examples) for an empty follow-up. Abandoned for a separate fork prompt; see "Round 2" |
| **v20** | v10 with only the reasons rule changed: "for uncertain, the likely ones if the evidence points to a problem, else []" (v10 said "any likely ones", yet its own uncertain example has `[]`) |

## Results (16 turns × 3 reps = 48 per run)

| ver | exp | verdict | reason hit | empty doubt | lang mismatch | tokens in |
|---|---|---|---|---|---|---|
| v1 | 83 | 45/48 | 18/18 | 1 | 1 | 616 |
| v2 | 84 | 48/48 | 16/18 | 0 | 4 | 1005 |
| v3 | 85 / 87 | 48/48 · 48/48 | 18/18 · 18/18 | 0 · 0 | 5 · 3 | 926 |
| v4 | 88 | 48/48 | 18/18 | 0 | 10 | 966 |
| v5 | 89 | 48/48 | 15/18 | 0 | 9 | 991 |
| v6 | 90 / 92 | 48/48 · 48/48 | 18/18 · 17/18 | 0 · 0 | 0 · 1 | 917 |
| v7 | 91 | 48/48 | 16/18 | 3 | 2 | 666 |
| v8 | 93 | 48/48 | 18/18 | 0 | 5 | 928 |
| v9 | 94 | 48/48 | 18/18 | 0 | 3 | 972 |
| **v10** | 95 | **48/48** | 17/18 | **0** | **1** | 986 |

Pairwise on the doubt sentence (48 pairs each; A wins / B wins / ties):

| A vs B | A | B | tie | note |
|---|---|---|---|---|
| v6 vs v3 | 14 | 28 | 6 | v6's doubts are thin and third person ("the user …") |
| v6 vs v8 | 16 | 20 | 12 | |
| v6 vs v1 | 18 | 22 | 8 | 3 of v6's wins are v1 verdict errors |
| v9 vs v3 | 26 | 15 | 7 | v9 wins every rejected English case on language; loses "我是問第二篇論文" 3/3 (English doubt) |
| v9 vs v6 | 31 | 9 | 8 | sweeps all 15 uncertain pairs |
| **v10 vs v9** | **23** | 17 | 8 | wins "我是問第二篇論文" 3/3 |
| **v10 vs v1** | **30** | 15 | 3 | baseline; 3 of v10's wins are v1 verdict errors |

Experiments 81, 82 and 86 are void. They ran while `ReviewVerdict.doubt` had
a default, so the JSON schema sent as `response_format` made it optional, and
the small tier left `doubt` empty in 47 of 48 outputs. All three fields are
now required.

What the outputs show:

- **v1** misreads the uncertain/rejected boundary on bare or hedged complaints
  (`amb-zh-bare-buduo` once, `amb-en-authors-meant` twice → rejected). The
  examples in v2+ fix it.
- **v2–v5: the small tier copies the doubt language of the example that shares
  the verdict.** With a Japanese rejected example (v2/v3), English rejected
  follow-ups got Japanese doubts. With an English rejected example (v5), the
  zh/ja rejected follow-ups got English doubts. An explicit language rule did
  not override the copying (v4 got worse).
- **v6** removes the copy source: an example paraphrases what the doubt says
  without writing it in the target language. Language mismatches drop to 1/96,
  but its doubts get thin and third person, and it loses the pairwise to v3.
- **v9** keeps v6's paraphrased examples and asks for a grounded,
  second-person doubt. It beats v3 and v6 clearly. Its one systematic slip:
  "我是問第二篇論文，不是 Transformer 那篇" got an English doubt 3/3 (the
  English model name in the follow-up).
- **v10** names the follow-up's *main* language. That fixes the case (3/3
  Chinese), and v10 beats v9 on the judge. The one remaining mismatch in 48 is
  a different zh case answered in English once.

## Why v10

It has perfect verdicts, no empty doubts, 1/48 language mismatches, and the
best doubt sentences (it beats v9, which beats v3 and v6). It costs 986 tokens
in, about 60 more than v3. v1 is cheaper (616) but misclassifies hedged
complaints, the exact cases that get asked of the user.

## Round 2: the uncertain-reasons rule and forks (v11–v20)

Two parked issues. v10's rule "any likely ones for uncertain" contradicted its
uncertain example (`reasons []`). And fork reviews (`user_followup = ""`) had
never been evaluated: on the 8-case fork bucket
(`corpus/feedback_review.fork.jsonl`) v10 rejects **all 40** fork runs, with
13 empty doubts.

Fork rules inside this prompt (deploy model, 3–5 reps; fork verdicts / 24 or 40):

| ver | fork verdict | core verdict | note |
|---|---|---|---|
| v11 / v13 | 10/24 · 10/24 | 48/48 | "skip step 1" still leaves supported forks `rejected` or `confirmed` |
| v12 / v14 | 9/24 · 13/24 | 48/48 | a single fork example is copied: all-uncertain (v12) or more confirmed (v14) |
| v15 / v16 | 15/24 · 15/24 | 48/48 | every supported fork → `confirmed` ("the answer is confirmed correct") |
| v17 | 12/24 | 48/48 | defines confirmed as "the answer is wrong" |
| v18 / v19 | 32/40 · 28/40 | 80/80 | best fork scores, but v10 wins the keyword doubts: v10 vs v18 37–22, v10 vs v19 36–22 |

Every fork fix in this prompt cost keyword-doubt quality, so forks moved to
their own prompt (`feedback_review_fork`, 40/40) and this prompt only got the
reasons fix.

**v20 vs v10** on the core bucket (5 reps = 80 runs each, exp 31 / 30):

| ver | verdict | reason hit | empty doubt | lang mismatch | tokens in |
|---|---|---|---|---|---|
| v10 | 80/80 | 29/30 | 0 | 1 | 986 |
| **v20** | **80/80** | **30/30** | 0 | **0** | 997 |

Pairwise on the doubt: **v20 33**, v10 23, ties 24. No verdict or reason
regression, so v20 ships as `feedback_review/v2`.

## Known limits

- The corpus is hand-authored (no reviewer runs exist yet to harvest). Once the
  background `feedback:review` run records real verdicts, harvest them into a
  `feedback_review.harvest.jsonl` bucket and re-sweep.
- Every variant labels `corr-en-decoder` with both `off_topic` and
  `wrong_source`, and `corr-ja-citation` with both `citation` and
  `wrong_source`. The labelled reason is included, but the reason lists run
  wider than the single label.
- The flash-lite tier occasionally emits a garbled CJK character (seen in v3
  and v8 outputs). This is a model artefact, not a prompt effect.

## Round 3: a claimed number the trace does not show (v21–v22)

Live run 782: the user wrote "wrong, I think the paper says 16 heads" under a
correct answer (h = 8). The only cited text was figure markup with no head
count, yet v20 returned `confirmed` with the doubt "the paper actually
specifies 16 heads". The model took the user's number, or its memory of the
paper, as evidence. The core bucket got five cases (21 in all):

- `claim-en-heads-markup` (run 782's recorded inputs), `claim-zh-dropout-thin`,
  `claim-ja-warmup-thin`: the cited text is cut before the number, `uncertain`;
- `claim-en-contradicted-by-cite`: the cited text gives the answer's value, not
  the claimed one, `uncertain`;
- `claim-zh-layers-shown` (guard): the cited text shows the claimed number and
  contradicts the answer, `confirmed`.

| ver | change |
|---|---|
| v21 | v20 + step 2: "A number or fact the user claims counts only if <trace_summary> shows it; never fill it in from what you know of the paper." |
| v22 | v20 + fork v6's "Name it only from what the question, answer and trace show, never from what you know of the paper", and step 3 lists "a claim the inputs do not show" as uncertain |

Deploy model `gemini/gemini-3.5-flash-lite`, 5 reps = 105 runs each (exp 180–182):

| ver | verdict | reason hit | lang mismatch | tokens in | misses |
|---|---|---|---|---|---|
| v20 | 100/105 | 30/35 | 10 | 991 | `claim-zh-dropout-thin` → confirmed 4/5, `claim-en-heads-markup` 1/5 |
| **v21** | **104/105** | **35/35** | 6 | 1020 | `claim-en-contradicted-by-cite` → confirmed 1/5 (its doubt quotes 0.1 twice) |
| v22 | 103/105 | 31/35 | 5 | 1021 | `claim-zh-dropout-thin` → confirmed 2/5 |

All three get the 16 earlier cases 80/80. Pairwise v21 vs v20 (105 pairs,
`gemini/gemini-3.8-flash`, A/B swapped): **v21 43**, v20 37, ties 25; six pairs
were decided by the verdict (5–1), the doubt sentences are close (38–36).

**Why v21:** it fixes run 782's case and the zh variant, keeps every earlier
verdict, hits every labelled reason, and beats v20 on the judge; v22 still
confirms the zh dropout claim. Language mismatches in this run come from the
model (v20 wrote English doubts for `corr-zh-second-paper` 4/5, which it did
not do in round 2), so the column is noisy at 5 reps.

## Round 4: full cited passages and the flagship tier

The reviewers now see every cited chunk in full, up to `CITED_TEXT_BUDGET` =
16,000 characters (it used to be the first 200 characters of each). The reason
and the measurements are in `../feedback_review_fork/README.md`, Round 4. Three
cases joined the core bucket (24 in all):

- `claim-en-heads-full-passage`: run 823 rebuilt with the full passage, which
  says `h = 8`, `uncertain`;
- `claim-zh-dropout-full-passage`: run 825's turn with "不對吧，dropout 應該是
  0.3", `uncertain`;
- `claim-ja-steps-wrong-full-passage` (guard): the answer gives the big model's
  300,000 steps, the passage gives 100,000 for the base model, `confirmed
  factual`.

The backslashes lost from `claim-en-heads-markup` and `claim-ja-warmup-thin`
were restored. `\begin` and `\beta` had become a backspace followed by `egin`
and `eta`.

v21 unchanged, 24 turns × 5 reps = 120 per run:

| model | exp | verdict | reason hit | empty doubt | lang mismatch | garbled zh/ja | misses |
|---|---|---|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 210 | 114/120 | 39/40 | 1 | 3 | 10/74 | `claim-zh-dropout-thin` → confirmed 2/5, `claim-en-contradicted-by-cite` 2/5, `claim-zh-dropout-full-passage` 1/5, one unparsable verdict |
| **`gemini-3.8-flash`** | 212 | **120/120** | **40/40** | 0 | 0 | **0/75** | none |
| **`gemini-pro-latest`** (this workspace's flagship setting) | 265 | **119/120** | **40/40** | 0 | 0 | **0/75** | `claim-ja-warmup-thin` → confirmed 1/5 |

Pairwise on the doubt (`gemini/gemini-3.8-flash`, A/B swapped), 3.8-flash (exp
212) vs flash-lite (exp 210): **78**–25, ties 17. Six of the 78 were decided by
a flash-lite verdict error.

No prompt change was needed. On both flagship models, v21 makes at most one
verdict error and writes no garbled doubts, so the keyword reviewer keeps `feedback_review/v3`
and only the default model changes.
