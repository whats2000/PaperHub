# agent_gather variants

The `gather` tool's reading subagent (`agents/core/gather.py`) is a
tool-using loop (`list_sections` / `search_chunks` / `read_section` /
`read_chunks`, then a `select` call), so the single-call sweep engine cannot
replay it. Variants were compared by running the real `gather` handler
in-process on the deploy small tier (`gemini/gemini-3.5-flash-lite`) against a
throwaway copy of the H4 workspace, 2 reps per (variant x brief), reading
every selection (ids, reasons, section, chunk size) and judging "do the
selected chunks answer the brief, is anything clearly relevant missed, any
dropped (unread) id".

| ver | change |
|---|---|
| v1 | baseline = shipped `agent_gather/v1`: 4-step workflow, read-before-select rule, one good/bad example |
| v2 | search-first (one search per needed fact), explicit recall rule (select the next chunk when it defines the symbols), cover every named paper, empty-result example |
| v3 | v2 + a coverage check before `select` |

## Briefs (6, real H4 papers)

b1 YOLOSeg loss + training setup for a sparsity regulariser (run-892 style, paper 24);
b2 X-VLA soft prompts + their training hyperparameters (16);
b3 Transformer positional encoding + why chosen (52);
b4 two-paper comparison of load balancing, Expert Choice vs Aux-Loss-Free (47, 48);
b5 V-JEPA 2 pretraining data + objective (35);
b6 BERT RL reward model (no such thing: must return empty) (68).

## Result (selected chunk ids; rep 1 / rep 2)

| brief | v1 | v2 | v3 |
|---|---|---|---|
| b1 | 82416,17,23 / 82416,17,18 | 82416,17,18 / same | 82416,17,18 / same |
| b2 | 82290,93,12,13 / same | 82290,12,13 / 82290,93,12 (13 dropped) | 82290,93,13 / 82290,93 (13 dropped) |
| b3 | 83659 / 83659 | same | same |
| b4 | 83581,83,02 (09 dropped) / 83584,83,00,01,02 | 83583,84,00,01,02 / 83583,84,01,02 | 83583,84,00,01,02 / same |
| b5 | 77582,89 / 77581,82,87,89 | 77582,87,89 / 77581,82,89 | 77582,89 / 77582,87,89 |
| b6 | empty / empty | empty / empty | empty / empty |

## Verdict

No variant beats the baseline cleanly. v2/v3 help the two-paper brief (b4)
once but regress b2 (they skip the chunk on how the prompts are used, and
select an unread id that gets dropped); v1 is the only variant whose b2
selection was complete both times, and its b4 miss (rep 1) is within the rep
noise v2 also shows. Every b6 run honestly returned an empty selection, and
no output contained non-verbatim text (content is rebuilt from the DB). Ship
`agent_gather/v1` unchanged.

Notes: a small model occasionally calls a nonexistent tool (`search_section`,
one run); the loop answers "Unknown tool" and it recovers. Chunks are large
(1-4 kB, a few are whole sections), so 2-5 selected chunks is the normal size.
The forced `select` fallback (`tool_choice` = the select function) was checked
live on the same model and returns a well-formed `select` call. The weakest
spot is a brief that names an optimiser/schedule (b1): it sits in a
non-obvious "Ablation studies" chunk found in only 1 of 6 runs.
