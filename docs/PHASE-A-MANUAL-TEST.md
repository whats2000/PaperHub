# Phase A — User Acceptance Test (frontend only)

**Pre-verified by the engineer:** all backend endpoints + Tier-1 LaTeX import (figures preserved on disk) + chat with Gemini citations confirmed working against the latest branch code (commit `190f466`). This document is your **user-facing acceptance test** — exercise it through the browser, no terminal commands beyond the two boot lines.

**Total time: ~5 minutes** (first run is ~30 s longer because the embedder model downloads once on the first paper QA).

If your `backend/.env` is configured (Gemini key in there), you're ready.

---

## Step 0 · Boot (one-time per session)

Open two terminal windows.

**Terminal A — backend:**
```powershell
cd backend
uv run uvicorn paperhub.api.app:create_app --factory --port 8765
```
Wait until you see `Uvicorn running on http://127.0.0.1:8765` (about 5–10 s).

**Terminal B — frontend:**
```powershell
cd frontend
npm run dev
```
Wait until you see `Local: http://localhost:5173/`.

Now open **http://localhost:5173** in your browser. You should see the PaperHub chat shell: dark background, left sidebar with a "PaperHub" header, central chat pane with a Composer at the bottom.

---

## Step 1 · Greet the bot (chitchat path) — *expect: polite refusal*

In the Composer, type:

> **`Hello!`**

Press Enter (or click Send).

**What you should see, in order:**
1. A small **routing badge** appears showing `chitchat · small` (the Router classified your message as off-topic; Gemini Flash made the call).
2. Within a second or two, an assistant message appears reading something like:
   > *"I can only answer questions about papers you have indexed in PaperHub. Please import some papers first, then ask a question about their content."*
3. **No error banner.** The whole exchange should finish in under 5 seconds.

✅ **Pass:** the routing badge says `chitchat`, the refusal text is the canonical one above.

---

## Step 2 · Import a paper — *expect: ~15 s wait, success message*

(The Phase A UI doesn't have a polished "Import Paper" form yet — that's Phase B work. For this UAT, paste the import command **once** into Terminal B's adjacent window so the paper lands in the indexed library; then we go back to the UI for Q&A.)

In a **third terminal** (or pause `npm run dev` momentarily — no, leave it; just open a new tab):

```powershell
curl -X POST http://127.0.0.1:8765/papers/import `
     -H "Content-Type: application/json" `
     -d '{"arxiv_id":"1706.03762"}'
```

**What should happen:**
- Wait ~15–35 seconds (network + LaTeX download + unpack + embed).
- The command returns a JSON blob with `"title":"Attention Is All You Need"`, `"extraction_tier":"latex"`, and `"notes_md":null`.

✅ **Pass:** the JSON shows `extraction_tier: "latex"` (Tier 1 succeeded; we got the real LaTeX source + figures, not the lossy markdown fallback).

> *Why no UI button for this in Phase A?* — Per the Phase A plan, paper import is a backend-only endpoint right now. Phase B adds the agentic "search arXiv by name → preview → import" flow inside the chat (the user just asks "find Attention Is All You Need" and the agent does the rest). Phase A ships the underlying import; the UI wiring is the Phase B addition.

---

## Step 3 · Ask the bot about the imported paper — *expect: cited answer*

Back in the browser at http://localhost:5173, in the same chat or a new one, type:

> **`What is the main architectural contribution of this paper?`**

Press Enter.

**What you should see, in order:**
1. The routing badge appears showing `paper_qa · flagship` (Router classified your question as paper-content, dispatching to the flagship model).
2. A collapsible **trace strip** (or chip) appears showing a single tool step: `research_agent · research_qa` — click it to expand and you'll see chunks-retrieved count + latency.
3. The assistant's answer appears, mentioning **the Transformer architecture**, with at least one inline citation marker that looks like `(§something, p.something)`.
4. Below the answer, a row of **citation chips** appears (one per retrieved chunk).
5. The whole exchange should finish in under 30 seconds (longer the first time only because the embedder downloads its model).

✅ **Pass:** answer mentions "Transformer", contains an inline `(§..., p....)` citation marker, and the trace strip shows a `research_qa` tool step.

---

## Step 4 · Ask a follow-up — *expect: same flow, faster*

In the same chat, ask:

> **`What advantage does the Transformer have over recurrent models?`**

Press Enter.

**What you should see:**
1. Same routing badge `paper_qa · flagship`.
2. New tool step in the trace.
3. Answer mentioning **parallelization** (or "more parallelizable" — the paper's own phrasing), **training speed**, and / or **reduced training time**, with citation markers.
4. Faster this time: ≤ 10 s.

✅ **Pass:** the answer is relevant + cited.

---

## Step 5 · Ask something the paper doesn't cover — *expect: refusal or honest hedge*

Type:

> **`Does this paper mention quantum computing?`**

Press Enter.

**What you should see:**
- Routing badge `paper_qa · flagship` (your question is *about* the paper, just on an off-topic).
- The assistant should respond with one of:
  - The canonical refusal: *"No relevant information found in the indexed papers."*
  - A grounded hedge that admits the paper doesn't address quantum computing.
- **It must NOT make up content about quantum computing in the paper.**

✅ **Pass:** the answer is either an explicit refusal or an honest "this paper is about X, not quantum computing"-style hedge, without fabricated claims.

---

## Step 6 · Teardown

- Browser: close the PaperHub tab.
- Terminal A (backend): `Ctrl+C`. The arxiv-latex-mcp subprocess should exit cleanly with it (no zombie warnings).
- Terminal B (frontend): `Ctrl+C`.

✅ **Pass:** both servers stop without errors.

---

## What this UAT covered

| Capability | Step |
|---|---|
| Backend boots; migrations apply; frontend serves UI | 0 |
| Router correctly classifies off-topic input | 1 |
| Off-topic UX is polite + bounded (no random LLM monologue) | 1 |
| Tier 1 LaTeX import (figures + .bib + .sty preserved on disk for Phase B's slide pipeline) | 2 |
| Router correctly classifies on-topic input | 3, 4, 5 |
| RAG retrieval + Gemini-grounded answer with inline citation markers | 3, 4 |
| Tool-trace UI surfaces the agent's reasoning | 3 |
| Multi-turn conversation in the same session | 4 |
| Refusal / honest hedge on off-topic questions about the indexed paper (no hallucination) | 5 |
| Clean shutdown — no zombie MCP subprocesses | 6 |

## What this UAT did NOT cover (Phase B / C work)

- Import-by-paper-name (agentic search → read → decide flow) — Phase B
- Slide generation (paper2slides-plus port: single-paper → Beamer with figures) — Phase B
- Multi-paper integrated slides — Phase B
- NL2SQL ("how many papers about X did I add this year?") — Phase B
- Cross-paper relation graph — Phase B
- Multiple projects + tagging — Phase B
- Marker container (high-fidelity PDF→Markdown) — Phase B
- Eval harness with LLM-as-judge — Phase C
- Real token-by-token streaming (Phase A emits the full answer in one event) — Phase B
- Batch import — Phase C

## If everything passed

Reply "merge it" (or whatever) and I'll move on to the finish-branch step (merge to main, push + PR, or keep).

## If something failed

Tell me which step number + what you saw vs what was expected. I'll triage and iterate before any merge.
