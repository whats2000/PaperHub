# router — follow-ups restate the subject (router/v6)

Shipped as `src/paperhub/llm/prompts/router_v6.yaml` = **v9e**. The round
before it (router/v5 = v8b, the 0-refs "which paper?" rule) follows below.

## Hypothesis

Live run 892 (an idea chat about porting GISC sparsity constraints into
YOLOSeg low-contrast defect segmentation): 「給我更詳細實驗方法」 was rewritten
as 「請給我更詳細的實驗方法內容」. The paper_qa subagents see only
`resolved_query`, so each read its own paper's method and the answer was
marked off_topic. A follow-up's rewrite must restate the subject under
discussion; a self-contained question, a topic switch and every other intent
must keep their current rewrite and route.

## Test set

`router.followup.jsonl` (12 cases, all with history): 5 targets (zh-TW run 892
with its real history, en, ja, zh-CN, an en idea port) graded by intent plus
`expect.query_has` (the subject must appear in `resolved_query`), and 7 guards
(a self-contained question in the same chat, a topic switch to
`paper_suggest`, thanks, slides, memory, library_stats, and an anaphoric
`paper_search`) with `expect.query_lacks` where a stale subject must not leak.
Plus every earlier bucket. Deploy model `gemini/gemini-3.5-flash-lite`,
5 reps, one call in flight.

## Variants

| ver | change |
|---|---|
| v8b | baseline (`router/v5`) |
| v9a | `resolved_query` field: for a follow-up, restate the subject |
| v9b | v9a + one English follow-up example |
| v9c | v9b + a `## Follow-ups` section: the paper readers see only `resolved_query`; never stand in "the enabled paper" for a named subject |
| v9d | v9a + the section, no example |
| **v9e** | v9c + name the subject briefly (no recap of the answer), keep the conversation's script |

## Results (score / tokens in)

| bucket | v8b | v9a | v9b | v9c | v9d | v9e |
|---|---|---|---|---|---|---|
| followup | 0.83 / 3395 | 0.85 | 0.93, 0.98 (2 runs) | 1.00, 0.98 | 1.00 | **1.00** / 3726 |
| core | 1.00 / 2174 | | | 1.00 | 1.00 | 1.00 / 2504 |
| regression | 1.00 | | | 1.00 | 1.00 | 1.00 |
| edge | 1.00 | | | 1.00 | 1.00 | 1.00 |
| multiintent | 1.00 | | | 1.00 | 1.00 | 1.00 |
| history | 1.00 | | | 1.00 | 1.00 | 1.00 |
| deictic | 1.00 | | | 1.00 | 0.98 ⚠ | 1.00 |

v8b misses `fu-zh-tw-gisc-method` 5/5 (「請提供更詳細的實驗方法內容」) and
`fu-en-lora-experiments` 5/5 ("…experiments of the enabled paper"). v9d sent
`dx-guard-topic-0refs` to `paper_search` once.

Pairwise judge `gemini/gemini-pro-latest` (`STAGE_RUBRICS["router"]` + case
rubric, the case history now in the request), A/B swapped, on followup:

| A vs B | A | B | tie |
|---|---|---|---|
| v9c vs v8b | 14 | 6 | 40 |
| **v9e vs v8b** | **20** | **2** | 38 |

v9c lost the both-correct `fu-zh-cn-ddpm-limits` pairs 0–4: it rewrote a
Simplified-Chinese conversation in Traditional script and recapped the earlier
answer into the query. v9e wins them 3–0, wins 10 of 10 target pairs on the
subject check, and loses only 2 `fu-guard-independent-question` pairs (both
variants sometimes expand "YOLOSeg" to the full title).

## Known limits

- `response_language` of the script-neutral 「那它的局限呢？」 is often
  "Traditional Chinese" in every variant (predates this change).

# router — 0-refs "which paper?" rule (router/v5)

Shipped as `src/paperhub/llm/prompts/router_v5.yaml` = **v8b**. The round
before it (router/v4 = v5b, multi-intent priority in code) follows below.

## Hypothesis

Live run 837: "give me an overview of this paper, then build a deck" with 0
papers attached went to `paper_search`; the parser found no request and the
synthesizer asked which paper — two LLM calls for nothing. A bare pointer
("this paper", "這篇", "この論文") with 0 refs must become `clarify`; a named
title, a title named in an earlier turn, and a pointer plus a topic must not.

## Fix

- **Code** (`require_subject` in `agents/router_policy.py`, run by
  `apply_router_policy`): `RoutingDecision.subject` is the paper or topic the
  request names; with 0 refs a `paper_search` / `paper_suggest` whose subject
  is empty becomes `clarify` with a "which paper?" question in the response
  language (en, zh-TW, zh-CN, ja; English otherwise). `subject` is the LAST
  schema field: placed before `confidence`, the model rambled in it
  (experiment 283).
- **Prompt:** asks for `subject` as a NAME; pointer words are never a subject;
  a pointer takes the name from the earlier turn that gives it.

## Setup

Deploy model `gemini/gemini-3.5-flash-lite`, 5 reps. New bucket
`corpus/router.deictic.jsonl` (11 cases, en / zh-TW / ja): 6 bare pointers at 0
refs → `clarify`; guards: "find the paper Attention Is All You Need" in en,
zh-TW (next to 這篇) and ja → `paper_search`, "this paper" after a turn naming
Mamba → `paper_search`, "papers like this one on diffusion distillation" →
`paper_suggest`.

## Variants and results (deictic score; every other bucket 1.00 for all)

| ver | change | deictic | tokens in |
|---|---|---|---|
| v5b | baseline (`router/v4`) | 0.71–0.76 | 1741 |
| v6a | Override A: "a pointer alone names nothing" | 0.76 | 1781 |
| v6b | v6a + a Japanese pointer → clarify example | 0.76 | 1909 |
| v7a | v5b + `subject` | 0.78 | 1865 |
| v7b | v7a + the v6a line | 0.80 | 1905 |
| **v8a** | v7b + `subject` defined as a NAME (pointer words → "") | **1.00** | 1923 |
| v8b | v8a + two pointer examples (unnamed → clarify, named earlier → search) | 1.00 | 2180 |

Prompt-only variants (v6) left the router treating "this paper" as a name;
v7's model copied the pointer into `subject` ("this paper", "這篇論文的重點"),
so the rule never fired. v8a fixed both, including the earlier-turn guard.

Pairwise (`gemini-3.8-flash`, A/B swapped): deictic v8a vs v5b **25–7**
(23 ties), v8a vs v8b 13–13 (29 ties); history and multiintent v8a vs v5b
3–6 on history (76 ties). The history losses are a script leak: v8a wrote
the Simplified-Chinese "这周我问了你多少个问题？" in Traditional script in 3 of
5 reps (and once in multiintent); v5b and v8b never did. **v8b ships**: deictic
1.00 like v8a, no script leak in any bucket, at +439 tokens over v5b.

## Live check

- v8a: run 849, the run-837 message at 0 refs: the model picked
  `paper_search` with `subject ""`, and the code rule made it `clarify`; no
  search ran. Runs 850 and 851: "what is the Mamba paper about?", then, with
  the paper disabled (0 refs), "summarise this paper" → `paper_search`,
  `subject "Mamba"`.
- v8b (shipped): run 853, the same message → `clarify` from the model itself,
  one step only. Runs 854 and 855: the Mamba pair → `paper_search`,
  `subject "Mamba"` both turns.

---

# Earlier: router — multi-intent priority in code (router/v4)

Shipped as `src/paperhub/llm/prompts/router_v4.yaml` = **v5b**. Earlier
variants: `v1` (old deploy), `v2a` (Plan G1), `v3`/`v3b`/`v3c` (Plan G2;
`v3c` shipped as `router/v2`), `v4a`/`v4b`/`v4c` (`v4c` shipped as
`router/v3`, history below).

## Hypothesis

Two router/v3 defects. (1) "give me an overview of this paper, then build a
deck" went to `paper_qa` in 1–4 of 15 reps although the prompt says
`slides > paper_qa`; every failing rep rewrote only the overview half
(experiments 165–179, e.g. "Give me an overview of the enabled paper."), so the
model routed the half it kept. (2) v3 translated the `resolved_query` of
"ありがとう、助かりました！" into English in 2 of 5 reps. Nothing else may move.

## Fix

- **Code** (`agents/router_policy.py`, pytest `tests/test_router_priority.py`):
  `RoutingDecision.also_requested` lists the other intents the message asks
  for; the router node promotes `intent` to the highest-priority one, except
  where Override A (0 refs: no `slides`/`paper_qa`) or Override B (slide
  attached: no `slides`) applies, never off `clarify`, never downwards. A
  promoted turn drops the rewrite, so downstream reads the full message. A
  rewrite that drops every CJK/kana/hangul character of the message is
  replaced by the message.
- **Eval parity:** `StageSpec.finalize` runs the same policy, so a sweep scores
  what the deploy node returns (`llm_intent` keeps the model's own pick).
- **Prompt:** asks for `also_requested` and keeps `resolved_query` in the
  message's language.

## Setup

- **Model:** `gemini/gemini-3.5-flash-lite` (`PAPERHUB_MODEL_SMALL`, `.env` and
  the DB `settings` row; `PAPERHUB_ROUTER_MODEL` unset).
- multiintent gains 3 cases (13): ja and zh-CN explain-then-deck → `slides`,
  and a guard "summarize this paper; I'll make the slides myself later" →
  `paper_qa`.
- 5 reps on every bucket; multiintent another 10 (15 in total). Pairwise judge
  `gemini/gemini-3.8-flash`, `STAGE_RUBRICS["router"]`, A/B swapped.

## Variants

| ver | change |
|---|---|
| v4c | baseline (`router/v3`), graded through the new policy |
| v5a | v4c + `also_requested` + "never translate" on `resolved_query` |
| **v5b** | v5a + one zh-TW explain-then-deck example (→ `slides`, full rewrite) |

## Results (score / tokens in)

| bucket | v4c | v5a | v5b |
|---|---|---|---|
| core | 1.00 / 1510 | 1.00 / 1594 | 1.00 / 1734 |
| regression | 1.00 / 1517 | 1.00 / 1601 | 1.00 / 1742 |
| edge | 1.00 / 1538 | 1.00 / 1622 | 1.00 / 1763 |
| multiintent (5 reps) | 0.92 | 1.00 | 1.00 |
| multiintent (10 more reps) | 0.95 | 1.00 | 1.00 |
| history | 1.00 | 1.00 | 1.00 |

v4c misses: `mi-qa-slides-paraphrase` 3/5 + `mi-qa-slides-zh-cn` 2/5 (and the
10-rep run similar); v4c lists nothing in `also_requested`, so the code cannot
help it. v5a's model still picks `paper_qa` on most explain-then-deck reps but
lists `slides`, and the code promotes every one. v5b's model picks `slides`
itself (`also_requested: ["paper_qa"]`), so its rewrite survives.

Pairwise (A vs B, wins–wins–ties):

| bucket | v5a vs v4c | v5b vs v4c | v5a vs v5b |
|---|---|---|---|
| history (85) | 16–4–65 | **14–1–70** | 3–6–76 |
| multiintent (130) | 35–20–75 | **69–12–49** | 14–54–62 |

v5a loses judged pairs to v4c on `mi-qa-slides-refs`: a promoted turn falls back
to the raw message ("this paper", lower case), which the judge rates below a
resolved rewrite. v5b keeps the whole request in `resolved_query` ("…, then
build a deck for it") and wins both buckets.

## Why v5b

Zero routing regression, every multi-intent case at 15/15, the best judged
`resolved_query`, at +224 tokens (~15%) over v3. No rep translated a CJK
message in this sweep. (A code guard that restored the raw message on a
translated or script-switched rewrite was later reduced to detection only:
restoring dropped the context a rewrite resolves — a bare "推薦幾篇" becomes a
full query, SRS v2.11 — and the answer language comes from
`response_language`, not the rewrite. The drift is recorded as
`query_language_drift` on the classify step; prompt choice, e.g. v8b over
v8a, is what keeps the rewrite's script.)

## Known limits

- With v5b the `memory`/`paper_search` rewrites of a compound message now keep
  the dropped half ("Note that I like concise replies, and suggest some
  diffusion papers."). `memory_op` extracts the fact to store and the search
  parser the paper name, so this is benign, and the judge prefers it.

---

# Earlier: router — chat-history counts (router/v3)

Shipped as `src/paperhub/llm/prompts/router_v3.yaml` = **v4c**. Earlier
variants: `v1` (old deploy), `v2a` (Plan G1), `v3`/`v3b`/`v3c` (Plan G2;
`v3c` shipped as `router/v2`).

## Hypothesis

`router/v2` sent questions about the user's own chat history ("how many chat
turns have I had", "我總共問過你幾次問題？") to `chitchat`. The recorded
`classify` steps (runs 709, 789, 795, 796, 808) give the reason every time:
"meta-question about chat history", because v2 lists *meta-question* under
`chitchat` and defines `library_stats` by papers only. The SQL agent answers
these questions once it gets them. The fix must not move greetings or
questions about the app itself out of `chitchat`, or change any other route.

## Setup

- **Model:** `gemini/gemini-3.5-flash-lite`, the deploy router model
  (`PAPERHUB_MODEL_SMALL`; `PAPERHUB_ROUTER_MODEL` is unset). The config used
  to name `gemini-3.1-flash-lite`, the small tier before it changed.
- **New bucket** `corpus/router.history.jsonl`, 17 cases: 12 chat-history
  counts that must be `library_stats` (en, zh-TW, zh-CN, ja; five are the
  misrouted production messages, one has refs enabled, one counts decks) and 5
  `chitchat` guards ("what can you help me with?", "你是誰？", thanks, how the
  routing works, a greeting).
- All existing buckets kept: core 8, regression 14, edge 5, multiintent 10.
- 5 reps; multiintent also re-run at 10 reps (15 in total) because it has
  failures that predate this change.
- Pairwise judge `gemini/gemini-3.8-flash` with `STAGE_RUBRICS["router"]`
  (judges `resolved_query` and `response_language` once both intents are
  right), A/B swapped.

## Variants

| ver | change |
|---|---|
| v3c | baseline (`router/v2`) |
| v4a | `library_stats` also covers the user's usage history (chats, questions, turns, decks); `chitchat` = greeting, thanks, off-topic, or a question about you/the app that needs no data lookup |
| v4b | v4a + one zh-TW example (a chat-history count → `library_stats`) |
| **v4c** | v4a + one English example, `resolved_query` kept verbatim |

## Results (score / tokens in)

| bucket | v3c | v4a | v4b | v4c |
|---|---|---|---|---|
| core | 1.00 / 1372 | 1.00 / 1418 | 1.00 / 1528 | 1.00 / 1510 |
| regression | 1.00 / 1379 | 1.00 / 1426 | 1.00 / 1536 | 1.00 / 1517 |
| edge | 1.00 / 1400 | 1.00 / 1447 | 1.00 / 1557 | 1.00 / 1538 |
| multiintent (15 reps) | 0.89 | 0.89 | 0.93 | **0.97** |
| history | 0.73 | 1.00 | 1.00 | **1.00** |

v3c's history misses: `hist-zh-tw-asked-count`, `hist-en-breakdown` and
`hist-en-today-refs` 5/5 → `chitchat`, `hist-ja-sessions` 4/5, plus single
`chitchat`/`clarify` slips. Every guard stays `chitchat` in all variants.

Multiintent misses (all `paper_qa` instead of `slides`, also in the baseline):

| case | v3c | v4a | v4b | v4c |
|---|---|---|---|---|
| mi-qa-slides-refs | 8/15 | 4/15 | 2/15 | 1/15 |
| mi-qa-slides-paraphrase | 9/15 | 13/15 ⚠ | 9/15 | 4/15 |

Pairwise on history (85 pairs; 23 decided by the intent):

| A vs B | A | B | tie |
|---|---|---|---|
| v4b vs v3c | 32 | 12 | 41 |
| **v4c vs v3c** | **34** | **8** | 43 |

## Why v4c

v4a fixes the routes but misses `mi-qa-slides-paraphrase` more often than the
baseline. v4b holds every case, but its zh-TW example leaks into
`resolved_query`: it adds "在所有對話中" to every rewrite and once wrote a
Simplified-Chinese message in Traditional script, so the judge prefers the
baseline on the pairs both route right (9–12). v4c routes every history case,
regresses no existing case (it misses the multiintent pair less than the
baseline), and wins the judge on both-correct pairs 11–8.

## Known limits

- v4c translated the `resolved_query` of "ありがとう、助かりました！" into English
  in 2 of 5 reps (`response_language` stays Japanese, so the reply language is
  unaffected).
- `mi-qa-slides-*` ("give me an overview of this paper, then build a deck")
  still goes to `paper_qa` sometimes; that predates this change.
