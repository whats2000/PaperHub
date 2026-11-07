# PaperHub H4 — Agent mode (design)

**Status:** design, awaiting approval · **Date:** 2026-11-08 · **Branch:** `feat/plan-h4-agent-mode`
**Builds on:** v2.41.0 (Plan H shipped). **Authority:** this document plus the SRS for everything it does not change.

## 1. Intent and why

PaperHub answers through a **router → fixed pipeline** flow: the router classifies a message into one intent, rewrites it into one `resolved_query`, and hands it to that intent's pipeline (paper_qa, paper_search, library_stats, slides, memory, …). Inside paper_qa, per-paper subagents gather evidence and a finalizer answers.

That flow breaks on ordinary research work. Run 892 is the seed case: in an idea chat the user asked 「給我更詳細實驗方法」 after a discussion of porting GISC sparsity constraints to YOLOSeg. The router's rewrite dropped the subject, the subagents that decide what to read never saw the conversation, and the answer described each paper's own method. v2.41.0 patched it (`router/v6`, a recent-conversation block for the subagents), but the cause is structural: **the parts that decide what context to gather cannot see the conversation, and the part that can see it cannot gather.**

R&D work is multi-step by nature: follow a thread, compare papers, hunt for evidence, go find what is missing, turn it into a plan or slides. H4 adds an **agent mode** that works like a conventional coworker agent (Claude Code / Cowork): **one core agent holds the conversation and, over as many steps as it needs, decides which tools to call to grab context or take actions**, then answers.

H4 is an **experiment** with a user-experience bar. On the H4 branch the agent **is the whole chat flow** (no mode toggle). The experiment must not affect the current version: v2.41.0 on `main` stays untouched and **demo-able at any time**, and H4 develops against a backed-up, separate workspace (§3.6). Both versions are measured on the same cases; **H4 merges only when the agent delivers the R&D experience** (§3.5).

## 2. Goals, non-goals, anti-goals

**Goals**
- G1. A core agent loop that reads, searches, queries, recalls and acts through tools across multiple steps within one turn, with the whole conversation in view.
- G2. The eight R&D scenarios in §8 work in agent mode: grounded, on the thread, honest about what it did not find.
- G3. The experiment never affects the current version: v2.41.0 can be demoed at any time from `main`, against its own data, while H4 is built and tested (§3.6).
- G4. Every step is traced well enough to reconstruct the turn from the DB (the record principle), including what went into the model's context.
- G5. A measured comparison of both modes, published with the plan's results; it is the evidence for the later default/retire decision.

**Non-goals (this plan)**
- Deleting the router / pipeline code from the repo. On the H4 branch the agent replaces them in the chat request path; their code stays (reused as tools, or unused) until H4 merges.
- Migrating or re-running old chats.
- Plan I's external MCP server / plugin packaging (#37, #39). H4's tool definitions are written so Plan I can expose them later, but exposing them is not in scope.
- New paper features (new reading UI, new ingestion).

**Anti-goals (a failure even if it technically works)**
- Answers shallower than today's pipelines. The F6.2 lesson (more-agentic slide generation produced shallower decks) applies; depth is gated, not assumed.
- Citing `[chunk:N]` the agent never read in this turn or an earlier turn's record.
- Cost or latency running away on simple turns (a greeting, a library count).
- Silent actions: anything that changes the user's library, memories, or decks without it showing in the chat.

## 3. Decisions (made in the design conversation)

These were decided by the user unless marked **(my call)**.

### 3.1 The loop
- One core agent per turn. Each step it sees instructions, tool definitions, the session state, the conversation, and this turn's tool calls and results; it either calls tools or answers. The turn ends when it answers without calling a tool, or when the budget is reached.
- The core agent runs on a **strong flagship model**, managed with precise context (§4).
- Parallel tool calls within one step are allowed **(my call)**; e.g. reading two papers' method sections at once.

### 3.2 Reading: the core agent reads itself, with an optional cheaper gatherer
- The core agent has the reading tools directly and reads paper sections into its own context; it cites `[chunk:N]` straight from those results. There is no separate synthesizer: the agent that read the material answers.
- It may instead call **`gather(papers, brief)`**: a subagent on the **small model** skims as many sections as it needs in its own context and returns **only the relevant chunks, verbatim, with their ids and a one-line reason each** — not a summary — so the core agent still cites real text. The **brief is written by the core agent**, which sees the whole conversation (this is what run 892 lacked).
- When to delegate is the core agent's choice, steered by the tool descriptions and checked by the eval: delegate broad or multi-paper searches and long papers; read directly when it knows where to look.

### 3.3 Permissions (the conventional tiers)
| Tier | Tools | Behaviour |
|---|---|---|
| Free | reading, `gather`, library search, library SQL, memory recall, web paper search | no confirmation |
| Do, show, undo | add a found paper to the chat, save a memory, create an idea chat | runs; a visible card / chip with undo; memory keeps its existing safety gate |
| Do if asked, else propose | generate or regenerate a deck, write speaker notes, run ideation "find new pairs" | runs when the user's message asked for it; otherwise the agent proposes it. Enforced as a prompt rule checked by eval cases, not a code guard; the proposal button sends a normal user message that starts a new agent turn **(my call)** |
| Always confirm | remove a paper from the chat, forget a memory | asks first, unless **auto-approve** is on (below). The turn ends with a confirmation card; clicking it runs the action directly (no LLM call, no paused loop) **(my call)**. Deleting a deck version is out: no such backend capability exists and H4 wraps existing ones **(my call)**. |
| Every turn | — | steps stream live; the existing Stop button cancels mid-loop |

**Auto-approve (user decision; the common agentic-chat pattern).** An on/off toggle under the chat. When an operation in a confirm tier comes up and the toggle is **on**, a classifier decides whether it can run without asking (e.g. removing a paper the user just named → runs; forgetting a memory the user never mentioned → still asks); when the toggle is **off**, the user is asked. The classifier's decision and reason are traced. Its default state and whether it is per chat or global are settled in the plan.

### 3.4 What the user sees
- Same streaming design as today: each tool call is a live row in the trace panel (tool, short argument summary, status, latency) over the existing `tool_step` SSE events; the answer streams as `token` events; cards (search results, SQL table, deck, idea links) appear when their tool returns.
- The routing badge becomes a turn summary for agent-mode turns (e.g. `Agent · read 2 papers · 4 steps · 95%`) **(my call on the wording)**; old turns keep their stored badge and trace.
- **No mode toggle (user decision).** On the H4 branch every chat turn goes through the agent. Every run records `mode` (`pipeline` for runs from before H4, `agent` after), so traces, feedback and costs stay comparable.

### 3.5 Experiment, merge gate and adoption
- The comparison is **H4 branch vs v2.41.0**, each on its own backend instance and workspace, on the same cases (§8).
- **Merge gate (user: "real user experience first").** H4 merges only when, on the deploy models, agent mode **passes the §8 scenarios** (each scenario's cases meet its rubric: correct, grounded, on the thread) **and is no shallower than the current flow** on the scenarios both modes can attempt (judged pairwise), **and does not regress** the existing `backend/benchmark/` cases run in agent mode. Pass thresholds per scenario are set in the plan before any prompt iteration, not after. The plan therefore includes prompt-iteration loops (`writing-agent-prompts`) until the gate is met.
- Merging H4 makes the agent the flow on `main`; deleting the now-unused router / pipeline code is a later cleanup plan. Cost and latency per turn are reported with the gate results.

### 3.6 Isolation, backup and demo-ability (user decision)
- **Backup first.** Before H4 touches any data, back up the workspace (`paperhub.db` + `papers_cache/`), and back up again before every H4 migration run.
- **Separate workspace for H4.** The H4 backend runs on a **copy** of the workspace (its own `PAPERHUB_WORKSPACE`, its own port), so H4 migrations and test chats never touch the data v2.41.0 serves.
- **v2.41.0 demo-able at any time.** `main` (and the `v2.41.0` tag / images) stays as released; the user can start it on the original workspace whenever needed. H4 migrations are additive (new columns / tables only) so, even if a database is shared by mistake, v2.41.0 still runs on it.
- The plan states the exact commands for: making the H4 workspace, backing up, starting each version, and restoring.

## 4. Context management (precise context)

Order is stable-first so the prefix is prompt-cacheable:
1. **Instructions** (fixed): role, tool-use rules, citation rule (cite only chunks read this turn or listed in an earlier turn's record), response-language rule (memory > router-style detection > UI language, as today), when to ask a clarifying question instead of guessing.
2. **Tool definitions** (fixed): short, precise descriptions.
3. **Session state** (rebuilt each turn, small, in descriptive tags): enabled papers (id, title, pdf/latex, section count — no content); the idea context in an idea chat; the attached slide if any; active memories (bounded, as `build_active_memory_block` today); UI language.
4. **Conversation**: earlier turns as user text + answer text + a one-line **work record** per turn (tools used, chunk ids read and cited). Earlier turns' full tool results are **not** kept; the agent re-reads by chunk id when a follow-up needs them. Very old turns beyond a threshold fold into a short summary.
5. **This turn**: every tool call and its full result until the agent answers.

Guards: a per-result cap (section reads are paged; `gather` returns selected chunks only); a per-turn ceiling on steps and tokens — on reaching it the agent answers with what it has and says what it did not get to. Exact numbers are tuned in the plan.

## 5. Tools

The existing capabilities become tools; H4 wraps them, it does not rewrite them. Proposed catalogue (names and exact signatures settled in the plan):

| Group | Tools | Wraps |
|---|---|---|
| Read | `list_sections(paper)`, `read_section(paper, section, page?)`, `search_chunks(query, papers?)`, `read_chunks(ids)` | paper_qa subagent tools / the SQLite `chunks` table / paperhub-papers MCP |
| Delegate | `gather(papers, brief)` | a new small-model gatherer built on the paper_qa subagent loop |
| Library | `query_library(question)` | the SQL agent (ReAct, read-only, `validate_read_only_sql`) |
| Discover | `search_papers(query)`, `add_paper(candidate)` | the paper_search pipeline (Parser → Discover/Resolve → Finalizer), the attach endpoint logic |
| Memory | `remember(fact, scope)`, `forget(id)` | memory gate + `add_memory_with_supersede`; recall stays in the session-state block |
| Session | `remove_paper(paper)` | the existing reference remove / disable logic (confirm tier) |
| Slides | `create_deck(brief)`, `edit_slides(instruction, pages?)`, `write_notes(language?)` | the report pipeline / deck-command flows. The deck is **built from the papers**; the brief (written by the core agent from the conversation) only steers which papers, angle and emphasis — the slide pipeline itself is not changed (user decision) |
| Ideation | `list_ideas(filter)`, `propose_ideas(problem)` | the ideation store / `ideation_propose` |

Every tool result that carries paper text carries chunk ids. MCP tools keep the meta-in-payload convention (`{"result", "_meta"}`, unwrapped via `normalize_mcp_result`).

## 6. Tracing (record principle)

- `runs.mode` (`pipeline` / `agent`) on every run.
- The trace is the training record (§7): it must be enough to rebuild the turn's tool-calling trajectory — ids for content in the library, full payloads for anything that cannot be re-fetched.
- One `agent:step` row per loop step: model, the token count of each context block (instructions, tools, session state, conversation, this turn), the tool calls emitted (name + arguments), and for each tool its result ids (chunk ids, paper ids, SQL row count, deck version …) and status. The final answer text on the last step. Not the rendered prompt.
- Each tool keeps or gains its own traced step as today (e.g. `gather` records the brief, sections read, chunk ids returned).
- `paperhub-replay --run-id N` shows the loop; the trace panel streams the same rows.

## 7. Data, UI and other surfaces

- **Schema:** `runs.mode` (old rows read as `pipeline`) plus whatever the trajectory records need (§6); additive, idempotent migrations only.
- **Chat UI:** the auto-approve toggle under the chat and the confirmation card (i18n in all four locales; enter/exit transitions per the UI rules).
- **Badge / trace:** §3.4.
- **Feedback (H2):** agent turns get stage attribution that fits a loop (which tool step went wrong). **HF export must be able to rebuild full training pairs for agent turns** (user decision): the tool-calling trajectory (each step's tool calls and results) plus the answer. So the trace stores what cannot be re-fetched and references what can: chunk / paper ids for library content (re-read at export time), but full payloads for ephemeral results (web search results, SQL result rows, gather selections, classifier decisions). Private-document redaction follows the existing export rules.
- **Settings:** the core agent model, the gatherer model and the budgets are settings with `.env.example` entries.

## 8. Evaluation

A new benchmark bucket of multi-turn R&D scenarios (each with history, the user message, and what a good answer must contain / must cite / must not claim), run through **both** modes on the deploy models and judged with the existing judge (correctness, grounding, thread continuity) plus cost and latency:

1. Follow-ups that build on the thread (run 892 is case 1).
2. Cross-paper comparison with every cell cited.
3. Evidence hunting with an honest negative.
4. From an idea to an experiment plan (baselines, datasets, metrics, setup from cited papers).
5. Find what is missing (web), add it (visible, undoable), read it, answer with it.
6. Reproduction checklist from scattered sections and tables, flagging what is unstated.
7. Checking a pasted claim against the sources.
8. Slides on what we discussed, built from the papers (the conversation steers the brief; the content comes from the papers).

Plus non-regression: the existing `backend/benchmark/` cases through the H4 build. Both versions run on their own backend instance and workspace copy (§3.6); the benchmark driver gains multi-turn cases (history + message) and the judge gains a thread-continuity criterion. Prompts (core agent, tool descriptions, gatherer) follow the `writing-agent-prompts` discipline: ≥2 variants × these cases × judged, on the deploy model.

## 9. Constraints (from the repo)

Binding conventions in CLAUDE.md: TDD; the record principle and enrich-then-diagnose; `writing-agent-prompts` for every prompt; frontend i18n in four locales and UI transitions; new env vars in `.env.example`; a migration for library-changing data; `uv` only; live real-API gate at plan-phase completion; `paperhub-merge-prep` to finish.

## 10. Left to the builder

Tool names and signatures; budget numbers (steps, tokens, page size, compaction threshold); how the loop is wired (LangGraph subgraph vs a plain async loop); how cancellation propagates; the scenario cases' exact wording and rubrics; the badge's exact wording; whether `search_chunks` uses FTS5 or the existing section navigation.
