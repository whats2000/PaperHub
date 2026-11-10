# core_agent variants

The H4 core agent (`agents/core/agent.py`, slot `core_agent/v1`) is a
multi-step tool-calling loop over the whole conversation, so the single-call
sweep engine cannot replay it. Variants were compared by running the real
`run_core_agent` loop in-process (full tool registry, no MCP) against a
throwaway copy of the H4 workspace (`paperhub-copy-workspace --src
D:/paperhub-h4-workspace --dest D:/paperhub-scratch-agent`) on the deploy
flagship tier (`gemini/gemini-3.8-flash`; gather on the small tier
`gemini/gemini-3.5-flash-lite`). Each multi-turn case runs turn by turn, the
earlier answers saved as messages with their work records, exactly as the
chat layer does. Judging: Claude read every answer and its trace (tool
calls, chunk ids read, citations removed) against three questions: on the
thread, grounded (every claim cited from a chunk it read, no invented ids),
honest (negatives stated, no guessed numbers). The full scenario sweep is
H4 Task 14; this is the baseline comparison.

Cases: `benchmark/agent/corpus/core_agent.dev.jsonl` (8 cases; scenario 1
follow-ups incl. run 892 with its real history from session 590, scenario 2
cross-paper comparisons, scenario 3 evidence hunts with a negative).

| ver | change |
|---|---|
| v1 | first draft: role, 3-step workflow, citation / honesty / action sections, two examples (run-892 follow-up, COCO negative) |
| v2 | shorter rewrite: thread-first workflow, read-directly vs `gather` rules, three examples (follow-up, comparison table, BERT negative) |
| v3 | v2 + proposals narrowed to the if-asked actions (own line, phrased as the user's request, usually none) |
| v4 | v3 + a stopping rule for negatives (search the key terms, read the 2-3 likely sections, then answer) |

## Result (per answered turn; steps = loop steps incl. the forced one)

| case | v1 | v2 | v3 | v4 |
|---|---|---|---|---|
| s1-run892 | pass ×2 (6/7 steps) | pass (9), proposal misused | **empty ×2** (see below), pass ×1 | pass ×2 (9/5) |
| s1-moe-other t2 "the other one?" | **fail ×1**: answered without reading paper 48, invented "DeepSeek-V2/V3", most cells uncited, 1 id removed; pass ×1 | pass ×2 | pass | pass ×2 |
| s1-vjepa (2 turns) | pass | pass | pass | — |
| s2-moe3 | pass (10) | pass, hit budget (13), named what it missed; 3 generic proposals | pass (12) | — |
| s2-flow | pass | pass; 3 generic proposals | pass | — |
| s2-jepa | pass | pass | pass | — |
| s3-bert-rl (negative) | pass | pass | pass | pass (4) |
| s3-yoloseg-coco (negative) | pass, hit budget (13 steps, 20 calls) | pass (10) | pass, hit budget (13, 20 calls) | pass ×2 (6 / 9) |

"Empty": the model returned no text and no tool call (`finish_reason=stop`,
0 completion tokens) after reading the sections directly. A plain identical
retry came back empty too; the loop now retries once with a one-line nudge,
which recovered both empties seen afterwards (v4 run892 rep 2, v4 yoloseg
rep 2). It is a model/loop issue, not specific to v3's wording.

## Verdict

**Ship v4 as `core_agent_v1`.** v1 broke the thread case that motivated H4's
anti-goals (answered a follow-up about paper 48 from memory, uncited). v2
fixed grounding but used `<proposal>` for generic "would you like..."
questions and packed three into one tag. v3 fixed proposals (none emitted in
8 cases). v4 kept every v3 pass and cut the negative hunts from 13 steps /
20 calls (budget hit) to 6-9 steps without losing the honest answer. Not yet
covered by this comparison: v4 on the comparison cases (v3 passed them; v4
only adds the negative rule) and multi-rep variance on every case — both are
Task 14's sweep.

## H4 Task 14c — latency sweep (G-d)

Goal: the merge gate's G-d (median wall time of the six `chitchat` /
`library_count` cases <= 2x v2.41) failed at 2.05x. The deploy agent model
resolved from the H4 workspace settings is `gemini/gemini-pro-latest`
(flagship tier, a thinking model). Every variant below was run
directly against that model with the real tool schemas
(`build_default_registry().schemas()`), one model turn per query (the
turn's first decision: answer or which tool), and every output was read.
Queries: the three G-d chitchat prompts, three library counts (one in
Chinese), one library list-and-act request ("which MoE papers ... add the
most recent and summarise its routing"), and two paper questions on
papers 47/48 (one starting with "thanks, ..." to catch a small-talk
misfire).

| ver | change |
|---|---|
| v5 | v4 + "Small talk, thanks or 'what can you do': reply in 1-3 sentences, without tools." |
| v6 | v4 with the answer step sizing the reply (greeting = a sentence, capabilities = a few lines) |
| v7 | v4 + small talk 1-2 sentences, "what can you do" one line per tool |
| v8 | v4 + small talk 1-2 sentences, "what can you do" one short paragraph naming every kind of tool |
| v9 | v8 + two examples: a library count answered by `query_library(..., answer_user=true)`; a one-sentence "what can you do" |

Cells give the latency range in seconds and, in brackets, the number of
runs (reps x queries). v8 has two cells where it ran in two sweeps (first
against v4-v7, then against v9).

| query | v4 | v5 | v6 | v7 | v8 | v9 |
|---|---|---|---|---|---|---|
| "What can you do?" (no papers) | 8.1-10.7 [3] (1.5-1.8k chars) | 4.7-5.8 [3] | 7.1-9.1 [3] | 7.2-7.9 [3] | 5.9-7.1 [3] / 6.2-6.6 [3] | 4.5-4.6 [3; a third run 18.7, network] |
| same, papers 47/48 enabled | 9.1-10.6 [3] | 4.0-5.7 [3] | 5.7-8.0 [3] | 8.1-9.2 [3] | 6.6-10.8 [3] / 5.4-6.2 [3] | 3.5-5.3 [3] |
| capabilities named (read+cite, compare, search/add, library, slides, ideas, memory) | all [6] | library + memory missing [6] | most [6] | all [6] | all [12] | all [6] |
| greetings, en + zh | 3.2-6.0 [6] | 3.2-4.2 [6] | 3.0-3.7 [6] | 2.8-4.0 [6] | 2.7-4.4 [6] / 2.7-4.3 [6] | 3.1-3.8 [5; one run hung 226 s, network] |
| paper questions on 47/48: first call | search_chunks / list_sections [6] | same [6] | same [6] | not run | same [4] / same [6] | same [6] |
| library counts (3 queries, one zh): `answer_user` | — | — | — | — | true [9] | true [9]; the zh question kept in Chinese 2/3 |
| list-and-act ("which MoE papers ... add the most recent ... summarise its routing"): `answer_user` | — | — | — | — | false [3] | false [3] |

v5 was fastest but dropped two capabilities (the gd-chat-02 judge fails
v2.41 for exactly that); v7 restored them as a bullet list and was slow
again. **v8 shipped as `core_agent_v2`, then v9 as `core_agent_v3`.**

`query_library`'s `answer_user` flag (tool description, not this YAML;
commit "let query_library answer the user directly") was picked from two
descriptions x 7 queries x 2 reps on the v1 prompt: four library counts
("How many papers are in my library?", "... were published in 2025?",
"... PDF uploads rather than arXiv papers?", 「我的文獻庫裡有幾篇2024年的論文？」),
the list-and-act request, "Find my library papers on JEPA and compare their
masking strategies." and "List my 2025 papers and tell me which one I
should read first for a talk on VLA models.". Both set the flag right 14/14 (true for the counts,
false for the three read/compare/recommend requests); T2 kept the Chinese
question in Chinese 2/2 (T1 2/2 Chinese too, one rephrased) and shipped.

- T1: the existing description unchanged, plus the parameter
  `"answer_user": {"type": "boolean", "description": "true when the
  library's answer alone fully answers the user's message (e.g. a count or
  a list of their papers): it is shown to the user as written and the turn
  ends. Then write `question` in the user's language."}`
- T2 (shipped): the parameter `"answer_user": {"type": "boolean"}` and the
  description extended with: "Set answer_user=true when that answer is all
  the user asked for (a count, a list): it goes to the user verbatim and you
  get no further step; ask `question` in the user's language then. Leave it
  false when you still need to read, compare or act."

Rejected: a thinking knob. `reasoning_effort="low"` vs default on v8, 5
queries x 4 reps: no reduction in reasoning tokens (capabilities 294-582
vs 335-484; greetings 90-190 vs 74-196) or latency (median within noise);
a 128-token thinking budget likewise. Not shipped as a setting.

### Fix round 1: v10 / v11 (multi-intent and example 5)

Review asked for (a) multi-intent coverage of the `answer_user` example and
(b) an example 5 that neither hard-codes an English sentence nor promises
slides unconditionally. Variants: v9 (= `core_agent_v3`, baseline), v10 (v9
with example 5 as a list of what the tools do, written "in the user's
language", slides "from the papers in the chat"), v11 (v10 + a negative
example: a count plus a content question is `answer_user=false`; its
wording is deliberately not one of the queries). 14 queries x 3 reps x 3
variants on `gemini/gemini-pro-latest`, every output read; the new queries
were "How many MoE papers do I have and how do they differ?", "How many
papers from 2024 are in my library, and what is a mixture of experts
anyway?", "How many experts does paper 47 use?" (papers 47/48 enabled) and
「我的文獻庫裡有幾篇論文？」 with `<ui_language>zh-TW`; the other ten are the v8/v9
set. Brackets = runs.

| query | v9 | v10 | v11 |
|---|---|---|---|
| MoE count + "how do they differ" | query_library false [3] | false [3] | false [3] |
| 2024 count + general MoE question | false [3] | false [3] | false [3] |
| "How many experts does paper 47 use?" | search_chunks [3] | search_chunks / list_sections / gather [3] | search_chunks / list_sections [3] |
| zh-TW library count | true, question kept in Chinese [3] | true, Chinese [3] | true, Chinese [3] |
| the three earlier library counts | true [9] | true [9] | true [9] |
| list-and-act MoE request | false [3] | false [3] | false [3] |
| "What can you do?" (en, both paper states), s | 5.0-9.0 [6] | 4.1-5.5 [6] | 4.6-6.7 [6] |
| 「你可以幫我做什麼？」 (zh-TW UI) | Chinese, all 7 capabilities [3] | Chinese, all 7, slides "from the chat's papers" [3] | not run |
| greetings, paper questions | unchanged [12] | unchanged [12] | unchanged [12] |

No variant misfired on the multi-intent queries, so the negative example
(v11) buys nothing and costs tokens. **v10 shipped as `core_agent_v4`.**

## Final fix wave: v12 / v13 / v14 (remember gate, memory ids, search description)

Carry-forward C: a plain introduction ("Hi, this chat is for my MoE
load-balancing reading group.", run 1134; run 1135 likewise) made the agent
call `remember` (the v4 rule "`add_paper` and `remember` may run when they
serve the request"). Same wave: `<memories>` now lists
`<memory id scope>` (forget needs the id), and the `forget` /
`search_chunks` descriptions changed (ids from `<memories>`; the search's
all-terms -> any-term -> CJK-substring matching). Those descriptions and the
memory format are shared by all three variants:

| ver | change |
|---|---|
| v12 | baseline: v10 (= `core_agent_v4`) system unchanged + the new descriptions |
| v13 | v12 with `remember` only on an explicit request or a preference for future answers; chat context said in passing is not a memory |
| v14 | v13 + example 6 (a chat introduction gets a welcome, no tools; "Remember that I prefer tables." gets `remember`) + a `remember` description matching the rule |

In-process `run_core_agent` (full registry, no MCP) on a fresh copy of the
H4 workspace, `gemini/gemini-pro-latest`, 10 queries x 2 reps, one run at
a time (memories are global state; a first concurrent attempt leaked a saved
"answer in Japanese" into other runs and was discarded). The intro queries
are worded unlike example 6: "Hello! This space is for the lab's sparse-MoE
journal club.", "Hey, we're using this chat for our weekly transformer-basics
study group.", 「嗨，這個聊天室是我們實驗室讀書會用的，主題是瑕疵分割。」.
Every output read.

| query | v12 | v13 | v14 |
|---|---|---|---|
| 3 chat introductions: `remember` called | **4/6** | 1/6 | **0/6** |
| "Please remember that I like short bullet-point answers." -> remember | 2/2 | 2/2 | 2/2 |
| "From now on, always answer me in Japanese." -> remember | 2/2 | 2/2 | 2/2 |
| "Forget that I'm an NTU student." -> forget(right id) | 2/2 | 2/2 | 2/2 |
| 「不要再記得我喜歡表格了」 -> forget(right id) | 2/2 | 2/2 | 2/2 |
| "Does BERT use a reward model?" honest no, cited | 2/2 | 2/2 | 2/2 |
| Expert Choice load balancing (zh-TW) cited, in Chinese | 2/2 | 2/2 | 2/2 |
| "Thanks, that's all for today!" no tools | 2/2 | 2/2 | 2/2 |

**v14 shipped as `core_agent_v5`.**

## Evidence pages: v15 / v16 (deploy `core_agent_v8`)

Page-mode workflow: `view_figure` returns the figure's page and its box,
`zoom` reads a panel or small print, `view_chunk` / `view_page` read a
formula as printed, and a claim read off an image cites the narrowest view
(`[view:P/N/box]`). Baseline: v7 (`core_agent_v7`, figure crops).

| ver | change |
|---|---|
| v15 | v7 for pages: one evidence bullet per kind of printed evidence (figure → page → zoom; a PDF paper's formula read as printed), the narrowest-view citation rule, example 6 zooms the panel |
| v16 | v15 + a numbered figure procedure (view_figure → zoom the named panel → view_page for what the figure list lacks), "where the page and the chunk differ, the page wins", and a formula example (a paper not in the suite) |

Gate: `benchmark/cases.h4-evidence.toml` (23 cases), live backends, the
figure-aware judge, every answer read and re-scored by hand where the judge
was lenient or inconsistent (it passes "could not find it" answers as
honest negatives, passed two "L-shaped hole" answers that contradict the
rubric, and scored identical "62.0% from Table 3" GR00T answers both ways;
those are scored here by one rule, revised 2026-11-10: it passes only when it
gives the published table's value AND tells the user the chart they asked
about is not in the paper — silently accepting the false premise fails). Arms: A = original parsing (`27b58ff7`,
un-migrated copy, v7); B = repaired crops (migrated copy,
`PAPERHUB_AGENT_EVIDENCE=crops`, v7); C = pages (migrated copy, v15 or
v16). Hand scores per run:

| arm / run | multi-panel (4) | Marker split (2) | dropped (2) | commented out (1) | small print (1) | formula wrong (3) | formula partial (1) | formula right (2) | formula flat (4) | regression (3) | total (23) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 0 | 1 | 1 | 0 | 1 | 2 | 0 | 2 | 3 | 3 | 13 |
| B run 1 | 3 | 1 | 2 | 0 | 1 | 2 | 0 | 2 | 3 | 3 | 17 |
| B run 2 | 3 | 0 | 2 | 0 | 1 | 3 | 0 | 2 | 3 | 3 | 17 |
| B run 3 | 3 | 1 | 2 | 0 | 1 | 2 | 0 | 2 | 3 | 3 | 17 |
| C v15 | 3 | 1 | 2 | 0 | 1 | 3 | 1 | 2 | 3 | 3 | 19 |
| C v16 run 1 | 3 | 2 | 2 | 0 | 1 | 3 | 1 | 2 | 3 | 3 | 20 |
| C v16 run 2 | 3 | 1 | 2 | 0 | 1 | 3 | 1 | 2 | 2 | 3 | 18 |
| C v16 final code | 2 | 2 | 2 | 1 | 1 | 3 | 1 | 2 | 3 | 3 | 20 |

Only the final-code C run told the user the GR00T chart is not in the
paper (1 of 8 answers across arms): answers rarely correct a false premise
in the question — an open honesty gap.

Misses common to every arm: the Ball Pushing hole read as "L-shaped" (a
rectangular hole in perspective) and paper 81's Eq. (2), which no arm found
in its flattened PyMuPDF text (search, not the page tools). C's two
category losses against B are single answers: v16 run 2 left one answer
uncited because the model wrote `[view:]` boxes of its own inside a page it
had viewed and the validator removed them — the validator now snaps such a
box to the smallest viewed region holding it (trace:
`view_citations_snapped`), and the final-code run has no such loss; the
final-code run's multi-panel miss zoomed both panels correctly but left out
the block count (C passed that case in its other three runs, B in all
three). What only C gets right, every run: the formulas Marker transcribed
wrongly (3/3 vs B's 2-3) or split (1/1 vs 0/1), read from the printed page
and cited as the region read.

Regression check (10 non-mutating cases of `cases.h4-scenarios.toml`:
chit-chat, a library count, follow-ups, comparison table, evidence hunts,
checklist, claim check): B 9/10 (the library count answered "cannot access
the database" without calling the tool), C v16 9/10 (an off-by-one chunk
citation of a quote in a text-only answer, no page tool involved).

**v16 shipped as `core_agent_v8`**, and the `crops` mode and its switch
were removed.
