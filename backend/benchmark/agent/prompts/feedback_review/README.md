# feedback_review — keyword-flag reviewer prompt (Plan H2)

Shipped as `src/paperhub/llm/prompts/feedback_review_v1.yaml` = **v10**.

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
| **v10** | v9 + "the doubt follows the follow-up's *main* language; English paper names inside it do not make it English" |

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
