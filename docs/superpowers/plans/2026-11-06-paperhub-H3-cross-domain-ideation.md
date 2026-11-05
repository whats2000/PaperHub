# Plan H3 — Cross-Domain Ideation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An R&D engineer who is stuck describes a problem and gets **cross-domain ideas**: a mechanism borrowed from another field, drawn on a graph of their library (papers + private documents), each citing the section it came from. They manage ideas through a lifecycle that doubles as ideation feedback, and can open a chat with the documents to dig in.

**Architecture:**
- **Mechanism cards:** each document gets a domain-free card built from its cached PaperDigest. The card's fields cite section names, which resolve deterministically to chunk ids (`read_section_chunks`).
- **Ideation agent:** reads all cards in one context (the library is ~60 documents) and proposes `problem ← mechanism` matches (problem mode) or `document → document` pairs (free exploration). It never re-suggests dismissed ideas.
- **Citation edges:** Semantic Scholar edges between library documents, fetched by a background loop with the v2.40 backoff.
- **Tracing:** every LLM step is traced in an internal session hidden from the chat list.
- **Frontend:** a hand-drawn SVG graph laid out with `d3-force` plus `d3-polygon` hulls, a drawer, a legend, a problem switcher, a kanban view, idea chat sessions, and the ideation block on the Feedback page.

**Tech Stack:** FastAPI + aiosqlite + LiteLLM `structured` + Tracer, Semantic Scholar client, G1 harness (`backend/benchmark/agent`), React 19 + zustand + **d3-force / d3-polygon (new, pinned)** + Base UI + lucide + react-i18next, pytest / Vitest + RTL + MSW.

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10 (UI rules), **§III-10.3 H3** (FR-20), acceptance #10; §III-10.2 for the ideation feedback block (moved here from H2). Visual base: `docs/superpowers/designs/2026-11-05-plan-h/` (Ideas page graph/kanban, drawer, legend, problem switcher, dismissal, ideation feedback block).

**Depends on:**
- H0 merged: page slot, `PageHeader`, motion primitives, `is_private`.
- H2 merged: the Feedback page, which this plan adds a block to, and `g1-inbox.jsonl`.

**Branch:** `feat/plan-h3-cross-domain-ideation`, created from `main` after H2.

**Not overnight-friendly.** Tasks 3 and 5 author prompts that must pass an eval gate needing the user's judgment and two papers the user names. Run H3 in the daytime.

## Global Constraints

- **Visual encoding (dataviz component):**
  - colour = domain, with **≤ 8 coloured domains** and the rest gray "Other"; colours are assigned so **spatially adjacent or overlapping domains pass the validator** in both themes;
  - multi-domain documents are split-filled;
  - size = idea count;
  - arrowhead = source → target;
  - status = line style + mid-edge badge (never colour);
  - gray hairlines = citations;
  - lock = private.

  Chart text uses text tokens.
- **Detail on demand:**
  - nothing is selected on entry;
  - the drawer is **fixed at 340 px**, slides with `transform` over the graph, and never squeezes it;
  - the graph auto-pans so both endpoints stay clear of the drawer;
  - the zoom control moves aside when the drawer opens, while the legend never moves.
- **Graph interaction:** wheel zoom around the cursor (50–300 %), drag to pan (a drag is never a deselect click), double-click zooms, and a zoom control with − / % / + / fit.
- **Legend:** a vertical card at bottom-left, grouped, collapsible with a height animation, idle semi-transparent and opaque on hover.
- **Problem-mode ideas:**
  - target = the problem (no domain, no chunk ids), and only the source cites a section;
  - free-exploration ideas cite both ends.
- **Manual domain edits are never overwritten** by re-analysis.
- **Motion** follows user expectation, and every exit animates:
  - dismiss fades the idea out, with Undo in the toast;
  - the reason form collapses on cancel;
  - expanded content is scrolled into view.

  See CLAUDE.md *UI transitions*.
- **Stay in the page;** the layout fills by flex. All strings use `t()` in four locales (new namespace `ideas`). Icons are lucide.
- **Prompts:** every prompt is authored by invoking **`writing-agent-prompts`**. **Card and ideation prompts ship only after the G1 gate:**
  - (a) the expected source is in the top 5 for **≥ 7 of 10** cases;
  - (b) the chosen variant beats the baseline pairwise.
- **Tracing:** ideation runs are traced as `ideation:*`, recording the card ids read, the pairs proposed and the evidence chunk ids. Rendered prompts are never stored.
- **Ops:** new env flags go in `.env.example` (one-line style); `uv` for Python; Conventional Commits with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; single user, so no keys or uniqueness that would block a later user column.
- **Done = automated gates green plus the eval gate recorded.** The live gate and merge-prep happen with the user.

## Review Focus

- **The ideation reply may name a document id or section that doesn't exist.** Such ideas are dropped and logged in the trace, and are never stored with fabricated chunk ids. Pinned in Task 5.
- **Re-running card analysis after the user edited a document's domains** must keep the manual tags. Pinned in Task 3.
- **A library with more than 8 domains** folds the extra domains into "Other" (gray) without repainting existing domains: colour follows the domain, not its rank. Pinned in Task 9.
- **Dismiss, then Undo within the toast,** restores the previous status exactly, including `trying`, rather than resetting it to `new`. Pinned in Tasks 6 and 11.
- **A user drag that ends over empty space** must not count as the click that closes the drawer. Pinned in Task 10.

---

## API contract (fixed by Task 1)

```
GET  /ideation/graph?problem_id=|explore=1 → { documents: [{ id, title, domains: [domain_id], idea_count, is_private }],
                                                domains: [{ id, name, color_index: int|null }],      # null = Other
                                                citations: [{ source_id, target_id }],
                                                ideas: [Idea], problem: Problem|null }
Idea    = { id, problem_id|null, source_doc, source_section, source_chunk_ids: [int],
            target_doc|null, target_section|null, target_chunk_ids: [int], title, rationale,
            status: "new"|"saved"|"trying"|"worked"|"failed"|"dismissed", prev_status, note,
            dismiss_reasons: [str], session_ids: [int], created_at }
Problem = { id, text, tried, created_at, idea_count, status: "generating"|"ready"|"error" }

GET    /ideation/problems                 → [Problem]
POST   /ideation/problems                 { text, tried } → Problem     # starts generation in the background
GET    /ideation/problems/{id}            → Problem
POST   /ideation/explore                  → { started: true }           # free-exploration generation
PATCH  /ideation/ideas/{id}               { status?, note?, dismiss_reasons?, dismiss_note? } → Idea
POST   /ideation/ideas/{id}/undo-dismiss  → Idea
POST   /ideation/ideas/{id}/chat          → { session_id, title, prefill, paper_content_ids }
PUT    /ideation/documents/{id}/domains   { domain_ids: [int], new_names: [str] } → document   # manual edit
PATCH  /ideation/domains/{id}             { name?, merge_into? } → domains
GET    /ideation/feedback-summary         → { save_rate, worked_rate, dismiss_reasons: [{reason, count}] }
POST   /ideation/feedback-to-g1           → { appended }
```

`DISMISS_REASONS = already_tried | not_feasible | analogy_fails | not_novel | other`

---

### Task 1: Contract — tables, models, types, internal session

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`, `backend/src/paperhub/db/migrate.py`, `backend/src/paperhub/api/sessions.py` (the list query excludes internal sessions)
- Create: `backend/src/paperhub/ideation/__init__.py`, `backend/src/paperhub/ideation/models.py`, `backend/src/paperhub/ideation/internal_session.py`
- Create: `frontend/src/types/ideation.ts`
- Test: `backend/tests/test_ideation_models.py`, `backend/tests/test_migrate.py`, `backend/tests/test_sessions_api.py`

**Interfaces:**
- Produces:
  - **New tables:**
    - `mechanism_cards(paper_content_id PK, card_json, digest_hash, model, created_at)`
    - `domains(id, name UNIQUE, color_index INTEGER NULL, created_at)`
    - `document_domains(paper_content_id, domain_id, manual INTEGER DEFAULT 0, PRIMARY KEY(paper_content_id, domain_id))`
    - `citation_edges(source_id, target_id, PRIMARY KEY(source_id, target_id))` holds library-internal edges only
    - `citation_external(paper_content_id, external_arxiv_id, direction, PRIMARY KEY(…))` stores external neighbours for later use
    - `citation_fetch(paper_content_id PK, fetched_at, status)`
    - `problems(id, text, tried, status, created_at)`
    - `ideas(id, problem_id NULL, source_doc, source_section, source_chunk_ids_json, target_doc NULL, target_section NULL, target_chunk_ids_json, title, rationale, status, prev_status, note, dismiss_reasons_json, dismiss_note, created_at, updated_at)`
    - `idea_sessions(idea_id, session_id, PRIMARY KEY(…))`
  - **New column** `chat_sessions.internal INTEGER NOT NULL DEFAULT 0`. `GET /sessions` adds `AND s.internal = 0`.
  - `ideation_session_id(conn) -> int` creates or returns the one internal session (title `"Ideation (internal)"`, `internal=1`). It is never purged as an orphan.
  - The Pydantic models and TS types for the contract, plus `MechanismCard{problem: CitedField, mechanism: CitedField, assumptions: list[CitedField], limitations: list[CitedField], domains: list[str]}` with `CitedField{text, sections: list[str]}`.
- [ ] **Step 1: Write failing tests.**
  - The tables and the column exist after `apply_schema`, and applying twice is idempotent.
  - The internal session is created once.
  - `GET /sessions` never lists the internal session.
  - `Idea` rejects an unknown status.
- [ ] **Step 2: Run.** `uv run pytest tests/test_ideation_models.py tests/test_migrate.py tests/test_sessions_api.py -k "ideation or internal" -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS. Then run `npm run typecheck`.
- [ ] **Step 5: Commit** with `feat(ideation): fix the ideation API contract and tables`.

---

## Track A — backend

### Task 2: G1 ideation stage and eval set

**Files:**
- Modify: `backend/benchmark/agent/stages.py` (register `IDEATION`), `backend/benchmark/agent/grade.py` (the judge request text uses `case.variables.get("user_message") or case.variables.get("problem")`)
- Create: `backend/benchmark/agent/corpus/ideation.core.jsonl`, `backend/benchmark/agent/ideation.eval.toml`
- Test: `backend/tests/benchmark_agent/test_ideation_stage.py`

**Interfaces:**
- Produces:
  - `IDEATION = StageSpec(key="ideation", trace_agent="ideation", trace_tool="ideation:propose", response_model=IdeationOutput, variables_from_args=…, output_summary=…, deterministic_score=_recall_at_5)`.
  - `_recall_at_5(output, expect)` returns 1.0 when `expect["source_doc"]` is among the first 5 proposed `source_doc`s; for free exploration, when the `{source, target}` pair is among the first 5. It returns `None` when `expect` is empty.
  - **The corpus: 10 cases from SRS §III-10.3.**
    - The 8 in-library pairs are resolved to `paper_content_id`s by title from the live DB at authoring time, with a script cell recorded in the README.
    - Each case's problem text is **the target paper's own problem statement**.
    - The **2 cases for the user's own examples** (anchor box → reranker; ICL → VLA image prompts) are `expect: {}` placeholders. They are labelled **"needs source papers: ask the user which two papers; ingest them via `POST /papers`"**.
- [ ] **Step 1: Write failing tests.** Recall is 1 when the source is in position 5 and 0 at position 6, a pair match counts for explore, and an empty expect gives `None`.
- [ ] **Step 2: Run.** `uv run pytest tests/benchmark_agent/test_ideation_stage.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement, and author the corpus.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(eval): add the ideation stage and its eval set`.

### Task 3: Mechanism cards and domains

**Files:**
- Create: `backend/src/paperhub/ideation/cards.py`, `backend/src/paperhub/ideation/domains.py`, `backend/src/paperhub/llm/prompts/ideation_card_v1.yaml`, `backend/benchmark/agent/prompts/ideation_card/` (≥ 2 variants + README)
- Modify: `backend/src/paperhub/config.py`, `settings_registry.py`, `.env.example` (`# 3. LLM model selection`), all four frontend `settings.json`
- Test: `backend/tests/test_ideation_cards.py`, `backend/tests/test_ideation_domains.py`

**Interfaces:**
- Consumes: `get_or_build_digest(paper_id, conn, asset, adapter, model=settings.report_section_model)`, which shares the slides digest cache; `read_section_chunks`.
- Produces:
  - `build_card(conn, paper_content_id, *, adapter, model, tracer) -> MechanismCard`. It makes one `structured` call on slot `"ideation_card/v1"`, with the digest sections in `<sections>` and the title and abstract in tags, inside a traced step `ideation:card`. The step records the paper id, the digest section names and the card. `CitedField.sections` names not in the digest are dropped.
  - The card is cached in `mechanism_cards` with `digest_hash`, and rebuilt only when the digest changes.
  - `assign_domains(conn, paper_content_id, names: list[str]) -> None`:
    - It normalizes names: casefold and trim, with an alias map seeded with common abbreviations (CV, IR, NLP, RL, robotics, ML theory).
    - It maps each name onto an existing domain when the normalized name matches, otherwise creates one.
    - **It never touches rows with `manual=1`, and adds no auto tags to a document that has any manual tag.**
  - `settings.ideation_card_model` comes from `PAPERHUB_IDEATION_CARD_MODEL` (small tier); `settings.ideation_model` comes from `PAPERHUB_IDEATION_MODEL` (flagship).
- [ ] **Step 1: Write failing tests** (stub adapter):
  - Unknown section names are dropped.
  - The cache hit skips the LLM.
  - A changed digest rebuilds the card.
  - "Computer Vision" and "cv" map to one domain.
  - Manual tags survive `assign_domains`.
- [ ] **Step 2: Run.** `uv run pytest tests/test_ideation_cards.py tests/test_ideation_domains.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the code.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Author the card prompt.** **Invoke `writing-agent-prompts`.** Write ≥ 2 variants and judge them pairwise on 8 documents from the eval cases for faithfulness to the digest and domain-free phrasing of the problem. Ship the winner and record the table in the README.
- [ ] **Step 6: Commit.** Commit the code as `feat(ideation): build cited mechanism cards and domain tags`, then the prompt as `feat(ideation): add the mechanism card prompt chosen by eval`.

### Task 4: Citation edges background job

**Files:**
- Create: `backend/src/paperhub/ideation/citations.py`; Modify: `backend/src/paperhub/app.py` (lifespan start/stop like the arXiv backfill at :160-172 / :224-239), `.env.example` (`# 8. External lookup services`)
- Test: `backend/tests/test_ideation_citations.py`

**Interfaces:**
- Consumes: `find_related(arxiv_id, mode="cites"|"cited_by", max_results=…)`, `next_delay` (`arxiv_metadata_backfill`).
- Produces:
  - `fetch_once(conn, *, related=find_related) -> bool`. It takes up to 10 non-private documents with an `arxiv_id` and no `citation_fetch` row, or one older than 30 days. It stores internal edges (neighbour `arxiv_id` matches a library document) in `citation_edges` and the rest in `citation_external`, and returns False on `SemanticScholarRateLimitError`.
  - `run_citation_loop(conn, *, stop, …)` is the same loop shape as `run_backfill`.
  - The env flag `PAPERHUB_CITATION_EDGES` (default `1`) has a one-line `.env.example` entry.
  - Private documents are never queried.
- [ ] **Step 1: Write failing tests.**
  - Internal and external edges are split correctly.
  - A private document is skipped.
  - Rate-limit returns False and writes no `citation_fetch` row.
  - A second pass skips fetched documents.
- [ ] **Step 2: Run.** `uv run pytest tests/test_ideation_citations.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(ideation): fetch library citation edges in the background`.

### Task 5: Ideation agent

**Files:**
- Create: `backend/src/paperhub/ideation/agent.py`, `backend/src/paperhub/llm/prompts/ideation_propose_v1.yaml`, `backend/benchmark/agent/prompts/ideation/` (≥ 2 variants + README)
- Test: `backend/tests/test_ideation_agent.py`

**Interfaces:**
- Produces: `generate_ideas(conn, *, problem_id: int | None, adapter, model, card_model) -> list[int]`.
  1. It ensures cards for every document; missing ones are built with `build_card`.
  2. It runs one traced step `agent="ideation", tool="ideation:propose"`. `record_args` holds every prompt variable (G1 harvest needs them): `problem`, `tried`, `cards_xml`, `rejected_xml`, `mode`. `cards_xml` lists one `<card doc="id">` per document. `rejected_xml` lists the problem's dismissed and failed ideas with their reasons.
  3. The step calls `structured` with slot `"ideation_propose/v1"`. The response model is `IdeationOutput{ideas: list[{source_doc: int, source_section: str, target_doc: int | None, target_section: str | None, title: str, rationale: str}]}`, ranked best first, with at most 8 ideas.
  4. Validation:
     - `source_doc` and `target_doc` must be library ids.
     - Sections must exist in that document's card citations.
     - In problem mode `target_doc` must be null; in explore mode the two documents must have no shared domain.
     - Invalid ideas are dropped and listed in the step result as `dropped`.
  5. Chunk ids are resolved with `read_section_chunks`. The result records `{proposed, kept_ids, dropped, card_ids_read, evidence_chunk_ids}`.
  6. Kept ideas are inserted with `status="new"`.

  `run_problem(problem_id)` and `run_explore()` run in the background (task set pattern) under the internal session's run, setting the problem `status` to `ready` or `error`.
- [ ] **Step 1: Write failing tests** (stub adapter):
  - Unknown documents and sections are dropped and recorded.
  - A problem-mode idea with a `target_doc` is dropped.
  - A same-domain explore pair is dropped.
  - Kept ideas carry the resolved chunk ids.
  - Dismissed ideas appear in `rejected_xml`.
  - An LLM error sets the problem to `error` and records an `error` step.
- [ ] **Step 2: Run.** `uv run pytest tests/test_ideation_agent.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the agent.** Run again. Expected: PASS.
- [ ] **Step 4: Author the prompt.** **Invoke `writing-agent-prompts`.** Write ≥ 2 variants plus a baseline. Run `python -m benchmark.agent.cli sweep --config benchmark/agent/ideation.eval.toml` against the live DB copy's cards. Record recall@5 on the 8 labelled cases and the pairwise results in the README. **The ship gate needs all 10 cases.** If the 2 user-example papers are not yet in the library, mark the gate *pending user input* in the README and stop the prompt work here; don't adjust the gate.
- [ ] **Step 5: Commit.** Commit the code as `feat(ideation): propose cited cross-domain ideas`, then the prompt as `feat(ideation): add the ideation prompt with its eval results`.

### Task 6: Ideation API, idea lifecycle, idea chat

**Files:**
- Create: `backend/src/paperhub/api/ideation.py`; Modify: `backend/src/paperhub/app.py` (include router)
- Test: `backend/tests/test_ideation_api.py`

**Interfaces:**
- Produces all endpoints in the contract.
  - **`GET /ideation/graph`:**
    - It returns domains with `color_index` assigned by Task 9's rule (computed server-side so it stays stable).
    - Problem scope returns only that problem's ideas and the documents they touch, plus their domains. Explore scope returns free-exploration ideas plus every document.
    - Only library-internal citation edges are returned.
  - **`PATCH` status:**
    - It stores `prev_status` before each change.
    - Dismiss requires at least one reason.
    - `undo-dismiss` restores `prev_status`.
  - **`POST /ideas/{id}/chat`** creates a session titled with the idea title, attaches the source and target documents (`INSERT OR IGNORE INTO papers`), links `idea_sessions`, and returns a `prefill` opening message. The prefill is a short text naming the idea, the two cited sections and the rationale. The user sends it, so the context stays visible and editable.
  - **`PUT /documents/{id}/domains`** marks the tags `manual=1`.
  - **`PATCH /domains/{id}`** renames, or merges into another domain, moving the tags.
- [ ] **Step 1: Write failing tests.**
  - Graph scoping by problem.
  - Undo restores `trying`.
  - Dismiss without reasons returns 422.
  - Idea chat attaches both documents and links the session, and a second call returns a new session that is also linked.
  - A domain merge moves the tags and deletes the old domain.
  - Manual tags are set.
- [ ] **Step 2: Run.** `uv run pytest tests/test_ideation_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(ideation): expose problems, ideas and their lifecycle`.

### Task 7: Ideation feedback summary and G1 export

**Files:**
- Modify: `backend/src/paperhub/api/ideation.py`, `backend/src/paperhub/feedback/export.py` (H2's inbox writer)
- Test: `backend/tests/test_ideation_feedback.py`

**Interfaces:**
- Produces:
  - `feedback-summary`: `save_rate` = ideas ever saved, trying, worked or failed ÷ all ideas; `worked_rate` = worked ÷ (worked + failed); `dismiss_reasons` counted.
  - `feedback-to-g1` appends one line per idea with a terminal or saved status to `workspace/exports/g1-inbox.jsonl`. The line carries `stage_hint: "ideation"`, `variables` = the recorded `ideation:propose` args of the run that produced it, and `observed` = the idea. `expect` is `{source_doc}` for saved/worked ideas and `{}` with `negative_reasons` for dismissed/failed ones. The append is idempotent per idea.
- [ ] **Step 1: Write failing tests** for the rates on a fixture, idempotent append, and the expect shape per status.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(ideation): turn idea outcomes into ideation eval data`.

---

## Track B — frontend

### Task 8: Ideas page shell, problem switcher, entry box

**Files:**
- Create: `frontend/src/store/ideation.ts`, `frontend/src/components/ideas/ProblemSwitcher.tsx`, `frontend/src/components/ideas/ProblemEntry.tsx`, `frontend/src/locales/{en,zh-TW,zh-CN,ja}/ideas.json` (register in `lib/i18n.ts`)
- Modify: `frontend/src/pages/IdeasPage.tsx`, `frontend/src/lib/api.ts`
- Test: `frontend/src/components/ideas/ProblemSwitcher.test.tsx`

**Interfaces:**
- Produces:
  - `useIdeationStore`: `scope` ("explore" | problem id), `view` ("graph" | "cards"), `filter`, `selected`, `graph`, plus `load()`, `submitProblem(text, tried)` (polls `GET /ideation/problems/{id}` every 2 s until it is not `generating`), and `patchIdea`.
  - **Header:** `PageHeader` with `Lightbulb` and "Ideas", plus a graph/cards segmented control.
  - **The switcher sits left of the entry box.** It lists "Free exploration", every problem (date, idea count) and "+ Describe a new problem". The entry box takes the problem and "what I already tried" (collapsible). Submitting creates the problem and switches to it, with a generating state shown in place.
  - **Filter chips with counts:** in progress / saved / trying / has result / dismissed.
- [ ] **Step 1: Write failing tests.**
  - The switcher lists problems with counts.
  - Submit creates, switches and shows generating, then ready.
  - The filter counts match the fixture.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): add the Ideas page with problems and filters`.

### Task 9: Graph layout and visual encoding

**Files:**
- Modify: `frontend/package.json` (add `d3-force@3.0.0`, `d3-polygon@3.0.1` and their `@types`, pinned)
- Create: `frontend/src/lib/ideaGraphLayout.ts`, `frontend/src/lib/domainColors.ts`, `frontend/src/components/ideas/IdeaGraph.tsx`; Modify: `frontend/src/index.css` (8 `--domain-N` tokens, light and dark)
- Test: `frontend/src/lib/ideaGraphLayout.test.ts`, `frontend/src/lib/domainColors.test.ts`, `frontend/src/components/ideas/IdeaGraph.test.tsx`

**Interfaces:**
- Produces:
  - `layoutGraph(graph, { width, height, seed }) -> { nodes: {id, x, y, r}[], hulls: {domainId, points}[] }`.
    - It is deterministic: a seeded `forceSimulation` run for 300 ticks synchronously.
    - A per-domain cluster force pulls each document to its domains' centroid, so multi-domain documents settle between clusters.
    - `r = 8 + 3·sqrt(idea_count)`.
    - Hulls come from `polygonHull` over the domain's nodes, padded by `r + 10`.
  - `assignDomainColors(domains, adjacency) -> Map<domainId, number | null>`.
    - Colour follows the domain: an existing assignment is kept.
    - New domains greedily take the lowest palette index whose pairs with every adjacent domain are in `PASSING_PAIRS`.
    - Beyond 8 domains, or with no passing index, a domain gets `null` (gray "Other").
    - Adjacency means the hulls intersect or come within 24 px.
  - **Step to produce `PASSING_PAIRS`:** load the **dataviz** skill. Take its reference palette's first 8 categorical colours, run `validate_palette.js` on every pair against the app's light and dark card surfaces, and commit the passing pair set as a constant together with the validator output.
  - **`IdeaGraph` renders SVG:**
    - Hulls use a light domain-tinted fill and a coloured outline, with the label in text colour.
    - Nodes are filled with the domain colour; multi-domain nodes are split-filled (two half-circle paths), and private nodes show a lock glyph.
    - Citations are gray hairlines.
    - Ideas are arrows from source to target, or to the dark problem node: solid for saved/trying/worked, dotted for failed/dismissed, with a mid-edge badge (lucide star / flask / check / x).
    - The selected idea is solid and bold, its endpoints get rings labelled source and target, and everything unrelated dims.
- [ ] **Step 1: Write failing tests.**
  - The layout is identical for the same seed.
  - A multi-domain node lies between its two cluster centroids.
  - Colours: an existing domain keeps its index when a 9th is added; the 9th is `null`; adjacent domains never share a non-passing pair.
  - Graph: a dotted dash array for dismissed ideas, and the split-fill renders two paths.
- [ ] **Step 2: Run.** `npx vitest run src/lib/ideaGraphLayout.test.ts src/lib/domainColors.test.ts src/components/ideas/IdeaGraph.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement**, including the validator step.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): draw the idea graph with validated domain colours`.

### Task 10: Graph interaction, drawer and legend

**Files:**
- Create: `frontend/src/hooks/usePanZoom.ts`, `frontend/src/components/ideas/IdeaDrawer.tsx`, `frontend/src/components/ideas/GraphLegend.tsx`, `frontend/src/components/ideas/ZoomControl.tsx`
- Test: `frontend/src/hooks/usePanZoom.test.ts`, `frontend/src/components/ideas/IdeaDrawer.test.tsx`

**Interfaces:**
- Produces:
  - **`usePanZoom(svgRef, { min: 0.5, max: 3 })`** returns `{ transform, zoomAt(factor, clientPoint), panBy, fit(bounds), panToKeepVisible(points, rightInset) , wasDrag() }`.
    - A pointer move of more than 4 px between down and up counts as a drag. `wasDrag()` lets click handlers ignore it.
    - Double-click zooms in by 1.5 at the pointer.
  - **`IdeaDrawer`:**
    - A fixed `w-[340px]`, positioned absolute on the right, entering with `translate-x-full` → `0` (`transition-transform`, ~200 ms) and exiting the same way.
    - It closes on ✕, on Esc and on an empty-space click that was not a drag.
    - Content depends on the selection: an idea, a document, a domain, or the problem (full text, what was tried, idea list).
    - On idea selection it calls `panToKeepVisible([source, target, labels], 340)`.
  - **`ZoomControl`** (− / % / + / fit) slides left by the drawer width when it opens.
  - **`GraphLegend`:** a vertical card at bottom-left, grouped (domain, relation, idea status, document). It is collapsible through `Collapsible`, `opacity-55` when idle and opaque on hover/focus, and **never moves** when the drawer opens.
- [ ] **Step 1: Write failing tests.**
  - The zoom clamps at 0.5 and 3.
  - Zoom-at keeps the cursor's graph point fixed.
  - A drag of 10 px makes `wasDrag()` true and the drawer stays open.
  - A click on empty space closes it, with an exit (still in the DOM right after, gone after 200 ms).
  - Esc closes it.
  - Selecting an idea calls `panToKeepVisible` with inset 340.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): explore the idea graph with zoom, pan and a detail drawer`.

### Task 11: Idea management, domain editing, kanban, idea chat

**Files:**
- Create: `frontend/src/components/ideas/IdeaDetail.tsx`, `frontend/src/components/ideas/DismissForm.tsx`, `frontend/src/components/ideas/DomainEditor.tsx`, `frontend/src/components/ideas/IdeaKanban.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx` (a `Lightbulb` marker on idea sessions), `frontend/src/types/domain.ts` + `store/chat.ts` (`SessionSummary.idea_id`), `backend/src/paperhub/api/sessions.py` (return `idea_id` from `idea_sessions`)
- Test: `frontend/src/components/ideas/IdeaDetail.test.tsx`, `frontend/src/components/ideas/IdeaKanban.test.tsx`, `backend/tests/test_sessions_api.py`

**Interfaces:**
- Produces:
  - **`IdeaDetail`:**
    - The source and target show their cited sections as links that open the Canvas at the chunk.
    - Status buttons: save, trying, worked, didn't work.
    - The note autosaves, debounced 500 ms, showing "Saving…" then "Saved".
    - "Open chat" or "Continue chat" calls `/ideas/{id}/chat`, then `addForkedSession`-style insertion of the backend session, `requestComposerText(prefill)`, and switches the page to chat.
  - **`DismissForm`:**
    - It expands in place (`Collapsible`) and is scrolled into view with `revealInScroll(…, "end")`.
    - It offers multi-select reasons plus an optional note. Cancel collapses it.
    - Confirm fades the idea out and shows a toast with **Undo**, which calls `undo-dismiss`.
  - **`DomainEditor`** (in the node drawer): tags are removable (×) and addable with autocomplete over existing domains. Clicking a domain gives rename or merge-into. Edits call the manual endpoints.
  - **`IdeaKanban`:** columns new · saved · trying · has result. Dismissed ideas show only under their filter. Cards show the status, dismissal reason, note and chat count. The scope matches the problem switcher.
  - **Sidebar:** sessions with `idea_id` show a lucide `Lightbulb` marker, mirroring the fork marker.
- [ ] **Step 1: Write failing tests.**
  - Note autosave sends one PATCH after typing stops.
  - Dismiss requires a reason; confirm, then Undo, restores the previous status.
  - Cancel collapses with an exit.
  - The kanban columns match the statuses, and dismissed ideas are hidden unless filtered.
  - "Open chat" prefills the composer and switches the page.
  - The backend `GET /sessions` returns `idea_id`.
- [ ] **Step 2: Run.** `npx vitest run src/components/ideas` and `uv run pytest tests/test_sessions_api.py -k idea -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): manage ideas and open chats from them`.

### Task 12: Ideation feedback block on the Feedback page

**Files:**
- Create: `frontend/src/components/feedback/IdeationFeedback.tsx`; Modify: `frontend/src/pages/FeedbackPage.tsx` (a separate block below the stage analysis)
- Test: `frontend/src/components/feedback/IdeationFeedback.test.tsx`

**Interfaces:**
- Produces: a block titled "How good are the ideas?". It shows the save rate, the worked rate (`—` when there are no outcomes) and the dismissal reasons as bars with counts. **Write to ideation eval data** posts `feedback-to-g1` and toasts the appended count. There is muted help text explaining that idea outcomes feed the ideation eval.
- [ ] **Step 1: Write failing tests** for the rates rendered, the empty worked rate shown as `—`, and the post plus toast.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): show ideation feedback on the feedback page`.

---

### Task 13: Join and gates

**Files:**
- Modify: `CLAUDE.md` (plan table: an H3 row), `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10.2 (the ideation feedback block ships in H3)
- Test: `frontend/src/pages/IdeasPage.test.tsx` covers the full MSW flow: describe a problem → generating → ideas drawn → select → save → dismiss → undo → open chat.

- [ ] **Step 1: Write the integration test and wire anything missing.** Run `npx vitest run src/pages/IdeasPage.test.tsx`. Expected: PASS.
- [ ] **Step 2: Backend gates.** From `backend/`: `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src`. Expected: green.
- [ ] **Step 3: Frontend gates.** From `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`. Expected: green.
- [ ] **Step 4: Commit** with `feat(ideation): connect cross-domain ideation end to end`.

**Stop here.** The session with the user covers:
1. Which two papers the user's examples come from; ingest them via `POST /papers`.
2. Finishing the eval gate on all 10 cases.
3. The live gate on `:8000`, on the user's workspace library:
   - cards built;
   - citation edges fetched;
   - a real problem → ideas with valid cited sections (trace `ideation:propose` via `paperhub-replay`);
   - idea chat opened.
4. Visual sign-off against the prototype.
5. `paperhub-merge-prep`.
