# agent_autoapprove variants

The auto-approve classifier (`agents/core/permissions.py`) is a single
structured call `{run, reason}` on the deploy small tier
(`gemini/gemini-3.5-flash-lite`). Variants were compared in-process (pure LLM
calls, no workspace writes): 12 cases x 2 reps at temperature 0, scored
against the expected `run` label, reading every miss.

| ver | change |
|---|---|
| v1 | baseline: rules + 4 examples; "anaphora counts when unambiguous from the arguments" |
| v2 | three-item checklist (asks for this change / names the same target / no extra scope), explicit "other paper named" example |
| v3 | v2 + "bare pointer or new preference is not a delete request" + a changed-preference example |

Cases: named paper -> run; "clean it up" -> ask; Japanese forget -> run; "what do
you remember" -> ask; different paper named -> ask; "remove that one" -> ask;
Traditional Chinese removal -> run; "forget everything about my advisor" -> run;
new preference -> ask; unrelated summary request -> ask; bare "yes" -> ask;
explicit delete of a memory -> run.

| ver | correct / 24 |
|---|---|
| v1 | 18/24 (missed "remove that one", "yes", new-preference: all approved) |
| v2 | 22-23/24 (new-preference case flips between reps) |
| v3 | 24/24 |

## Held-out round (fix round 1)

The first round's shipped prompt (v3) carried examples that mirrored dev cases,
so its 24/24 was inflated. Round 2 adds a held-out set and a prompt whose
examples appear in neither set.

- `corpus/agent_autoapprove.dev.jsonl` (12 cases, the set above) and
  `corpus/agent_autoapprove.heldout.jsonl` (15 new cases phrased differently:
  aliases, Japanese / Chinese, hedged and retracted requests, a preference
  change, bare "ok go ahead").
- Runner: `uv run python -m benchmark.agent.autoapprove_eval --variants v1 v3 v4 v5 --sets dev heldout --reps 3`
  (deploy small tier, temperature 0).

| ver | change | dev (36) | held-out (45) |
|---|---|---|---|
| v1 | baseline | 27 | 40 (misses: "ok go ahead", "stop using APA, switch to IEEE" approved) |
| v3 | v2 + bare-pointer rule; examples overlap dev (shipped as registry `v1`) | 36 (inflated) | 45 |
| v4 | v3 rules, six NEW examples outside both sets | 36 | 45 |
| v5 | v3 rules, no examples (zero-shot control) | 36 | 45 |

## Verdict

The rules (checklist + bare-pointer rule) carry the win: v3, v4 and v5 are all
perfect on the held-out set, and only v1's lenient rule fails. The held-out set
no longer discriminates among them (ceiling), so v4 is shipped as
`llm/prompts/agent_autoapprove_v2.yaml` (new version file, call site switched;
`_v1` stays diffable): same score as v3 without eval-mirroring examples, and the
examples guard the ambiguous corners if the model changes. Failure mode is
fail-closed either way. Harder held-out cases should be added when the
classifier model changes.

## Final fix wave: production input format (review #4 / #12)

Production used to send `remove_paper(paper=4 ('Segment Anything'))` /
`forget(memory_id=12)` while the corpus used `"Segment Anything" (paper 4)` /
`memory 12: "lives in Taipei"`, and the memory text was missing entirely.
`permissions.target_summary` now emits the corpus format (memory text looked
up), and `permissions.approve_variables` XML-escapes the summary and the user
message. The runner builds every case through `approve_variables`, so the
eval reads exactly what production sends. Three held-out cases added: h16/h17
(text with `<`, `&`), h18 (a forget request naming a different memory).

`--variants v4 v5 v1 --sets dev heldout --reps 3`, `gemini/gemini-3.5-flash-lite`:

| ver | dev (36) | held-out (54) |
|---|---|---|
| v4 (= registry `v2`, shipped) | 36 | 54 |
| v5 (zero-shot control) | 35 (d8 "forget everything about my advisor" asked) | 54 |
| v1 | 27 | 48 |

No prompt change: v2 stays.
