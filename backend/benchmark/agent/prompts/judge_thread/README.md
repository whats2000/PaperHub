# judge_thread — the benchmark judge's thread-continuity criterion (H4 Task 14a)

Shipped as `_SYSTEM` / `_USER_TEMPLATE` in `backend/benchmark/judge.py` = **v2**.
The pairwise judge used by `runner.py --compare` is unchanged (see the second
round below).

## Hypothesis

The H4 merge gate scores a case 1 only when the answer is correct, grounded and
**on the thread**. The old judge never saw the earlier turns, so a follow-up
("more detail on that", "the second one?") answered about the wrong paper, or
generically, could still score 1. Adding the seeded turns and a thread rule
must catch those, without penalising an honest "the sources don't say", a
legitimate topic switch, a single-turn case or a slides case.

## Test set

18 hand-made cases (question, prior turns, answer, cited chunk text, rubric),
no server; chunk text written to support or not support each answer:

- **On-thread, expect 1:** detailed GISC experiment setup after a GISC chat;
  the same in zh-TW; ReMoE limitations after "what about the second one?";
  a test plan for porting GISC into YOLOSeg after that idea chat; an explicit
  topic switch ("Separately — how does ReMoE…").
- **Off-thread, expect 0:** generic "how to design experiments"; software unit
  tests instead of an experiment plan; Expert Choice details (well grounded)
  when the chat was about GISC; the first paper's limitations when asked about
  the second (well grounded).
- **Honest-missing, expect 1:** learning rate / number of experts the chunks
  do not state, answered "the source doesn't give it".
- **Grounding/regression, expect 0 or 1:** a fabricated learning rate; a
  single-turn answer with an invented auxiliary loss; a single-turn correct
  answer; a 10-slide deck as asked (1) and a 2-slide deck when 10 were asked
  (0, final check only).

How the 18 add up: 16 cases ran in the variant comparison — 10 with a rubric
that names the subject and 6 with a neutral rubric ("answer the latest request
in the context of the conversation…", so the thread has to come from the
turns) — and the 2 slides cases were added for the final check of the shipped
prompt only (16 + 2 = 18).

Deploy judge model `gemini/gemini-2.5-pro` (`DEFAULT_JUDGE_MODEL`), temperature
0, 2–3 reps.

## Variants

| ver | change |
|---|---|
| v0 | baseline: the previous judge (no prior turns shown, one 0/1 score) |
| v1 | v0 + a `## Prior turns` block + one rule: "when prior turns are given… score 0 if the answer ignores the conversation's subject… a self-contained new question may change topic" |
| **v2** | rewrite: direct first line; three 0/1 criteria (`correct`, `grounded`, `on_thread`) returned separately, score = all three; data in `<prior_turns>`, `<request>`, `<rubric>`, `<deck>`, `<answer>`, `<cited_chunks>` tags; three short on-thread examples (follow-up on subject, "second one", explicit topic switch) |

## Results (agreement with the expected score)

| set | v0 | v1 | v2 |
|---|---|---|---|
Each cell is judgements agreeing / judgements made (cases × reps).

| set | v0 | v1 | v2 |
|---|---|---|---|
| subject-naming rubric (10 cases × 2 reps = 20) | 20/20 | 20/20 | 20/20 |
| neutral rubric (6 cases × 3 reps = 18) | **12/18** | 18/18 | 18/18 |
| shipped v2 in `judge.py`: the 16 + 2 slides cases × 1 rep = 18 | | | 18/18 |

v0 scored the off-thread cases 1 (`h-otherpaper-deep`, `h-second-off`: a
correct, grounded answer about the wrong paper) once the rubric stopped naming
the subject. That is the failure the gate must catch.

v1 and v2 agree on every case. v2 ships: its verdict says **which** criterion
failed (e.g. `c0 g1 t0` for a grounded answer about the wrong paper,
`c0 g0 t1` for a fabricated value on the right subject), which the gate report
shows per case, at the same size (system prompt 255 words vs v1's 258; v1
also keeps the router-intent fields the H4 flow no longer has). After the comparison, v2 gained one
slides clause carried over from v0 (a deck that ignores an explicit slide
count is not correct); the final 18-case check above includes both slides cases.

## Round 2 — pairwise rubric for `--compare` (G-b)

Hypothesis: the pairwise judge (`benchmark.agent.grade.judge_pairwise`) needs an
answer-depth rubric to prefer a deeper grounded answer over a shallow or
off-thread one.

| ver | rubric passed to `judge_pairwise` |
|---|---|
| **p0** | baseline: none beyond the case's own rubric |
| p1 | + "Better, in order: (1) correct and supported by its own cited chunks…; (2) on the conversation's subject; (3) deeper — specific facts, numbers and settings… Length alone is not depth." |

Six pairs × both orders × 2 reps on `gemini/gemini-2.5-pro`: deep vs shallow,
deep vs verbose-but-shallow, shallow-but-on-topic vs generic, honest-missing vs
fabricated, on-thread vs grounded-but-wrong-paper (expected A each), and two
equivalent answers (expected tie).

| | p0 | p1 |
|---|---|---|
| matches expected | 24/24 | 24/24 |

p1 does not beat the baseline, so no new pairwise prompt text ships: the runner
passes the case rubric only. The runner wraps the request as
`<conversation>` + `<request>` and each side as `<answer>` + `<cited_chunks>`
(data layout only).

## Round 3 — drift on old cases (fix round 1)

Question: does v2 judge the existing single-turn regression cases (G-c) the
same way as v0? Input: the 20-case `cases.example.toml` run
`judged-20cases.json` from the main checkout's `benchmark/results/` (read-only
copy), re-judged with v0 and v2 on `gemini/gemini-2.5-pro`. On these cases
the score is correct AND grounded, with on_thread always 1.

| chunks shown to the judge | reps | v0 vs v2 agreement (per rep) |
|---|---|---|
| cut at 1200 chars (the judge as it was) | 2 | **24/40** |
| whole chunk (after `fix(benchmark): show judges the whole cited chunk`) | 3 | **58/60** |

**With the 1200-char cut, v2 drifted badly.** It failed qa-01, qa-04, qa-07
and qa-10 on grounding in both reps where v0 passed them. It split between
reps at temperature 0 on qa-03, qa-05 and qa-09. It passed qa-02 and qa-14,
which v0 failed. Reading the cases showed both judges were misled by the cut:
the sentence supporting the claim sat past char 1200 of a long chunk (qa-01's
"computational cost is similar…" at char 1948; qa-02's sinusoid formula at
char 2907). v2 then flagged supported claims as ungrounded, and v0 flagged
qa-02 although the support was there. Chunks run to 5000 chars, so the cut was
a harness bug, not a prompt property. It is now fixed for the judge and the
pairwise judge.

With whole chunks the two judges agree on 58 of 60 judgements; the 2
remaining disagreements:

- qa-05 (rep 3): v2 marked `correct = 0` for an unrequested extra paragraph
  on a third method. The other two reps gave 1. This is a slight, unstable
  extra strictness on "does what the request asks".
- rpt-04 (rep 1): v0 gave 1, then 0 on reps 2–3. v0 itself is unstable there;
  v2 gave 0 all three times.

Both judges now pass qa-02 and qa-14, which the stored verdicts (made with
the cut) failed. So pass counts from runs judged before this fix are not
comparable with new ones. G-c compares H4 and v2.41 judged in the same run,
so the gate is unaffected. No prompt change was made in this round.

## Negative cases (2026-11-10): v3

The judge passed "I could not find Equation (2)" answers as honest negatives
although the rubric gave the equation (paper 81, every arm), and passed a
half-written equation. Three variants of the `correct` criterion, 12 saved
answers with hand labels (5 wrong "not found"/partial, 7 that must pass:
two honest negatives, five correct answers) x 2 reps, `gemini/gemini-2.5-pro`,
temperature 0:

| variant | change | agree with hand labels |
|---|---|---|
| A (v2) | "a negative case ... saying so honestly is correct" | 19/24 (5 wrong "not found" passed) |
| B | a negative case is one the rubric marks as such; when the rubric gives the answer, "not found" or a partial answer is wrong | 22/24 |
| C | B + three examples | 22/24 |

B and C disagreed with the labels only on one GR00T answer (the right value
from Table 3, but no word that the bar chart the user asked about is not in
the paper); re-reading it, the judges were right and the hand label wrong —
silently accepting a false premise is not a correct answer. With the
corrected label both are 24/24. **B shipped as v3** (the shorter one).
