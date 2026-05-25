# PaperHub — screenshot set

Capture checklist for the project presentation + README imagery. Drop PNGs in
this folder named exactly as the **File** column so they sort into demo order
and the README/slides can reference them by a stable path.

## Capture conventions

- **Window size:** a consistent **1440×900** (or 1280×800) for every shot.
- **Theme:** pick one (light *or* dark) and keep it across all shots.
- **Redact** any real API keys / personal paths if visible.
- **Format:** PNG. Keep filenames lowercase-kebab, `NN-feature-state.png`.
- For before/after shots, either a split image or two files (`…-before.png` /
  `…-after.png`).
- ★ = lead with these; they're the most distinctive.

Tip: session **112** on a running `:8000` already has a generated deck with
Traditional-Chinese notes — shots **11–14** can be taken without regenerating.

## Shot list (demo order)

### Shell + observability
- [ ] `01-app-overview.png` — full window: sidebar + chat + composer.
      *Caption:* "One chat shell; every turn routed to a specialist agent."
- [ ] `02-routing-badge.png` — a turn showing the routing badge (intent + model).
      *Caption:* "Visible routing — see which agent + model handled each turn."
- [ ] ★ `03-trace-panel.png` — expanded trace DAG of a turn (per-step latency/status).
      *Caption:* "Every model/MCP/pipeline step is an audit row — the full DAG replays from SQLite."

### Grounded RAG — the provenance hero
- [ ] ★ `04-citation-canvas-html.png` — an answer with `[chunk:N]`, clicked, the
      side panel scrolled to + highlighting the chunk in the LaTeX-rendered HTML.
      *Caption:* "Click any `[chunk]` → the exact passage, highlighted in the rendered paper."
- [ ] ★ `05-citation-canvas-pdf.png` — same citation highlighting the passage in the
      source PDF (geometry overlay on the text layer).
      *Caption:* "…and in the original PDF. No ungrounded claims."
- [ ] `06-language-adherence.png` — a Chinese question answered in Chinese, with
      citation markers + paper titles preserved.
      *Caption:* "Answers in your language; citations stay verbatim."

### Discovery + references
- [ ] `07-paper-search-cards.png` — a `paper_search` turn with Add-as-reference
      cards (include an "Added by agent ✓" card).
      *Caption:* "Discovery via web + Semantic Scholar; the agent auto-adds its best picks."
- [ ] `08-reference-sources.png` — the Reference Sources drawer with enabled papers + toggles.
      *Caption:* "Session-scoped reference set with per-paper enable/remove."

### Library intelligence + memory
- [ ] ★ `09-library-stats-sql.png` — "How many papers do I have?" answered with the
      numbers **and the read-only SQL it ran**.
      *Caption:* "NL→SQL over a read-only allowlist — answers with the exact query."
- [ ] `10-memory-manager.png` — the Memory Manager: Session / Global groups, a
      supersede chain (active/superseded badges).
      *Caption:* "Session + global memory with a safety gate and conflict-supersede history."

### Slide pipeline — the new headline (F4)
- [ ] ★ `11-slides-generate.png` — deck chip (slides-only, **"Generate notes"** button)
      + the Slides panel open (filmstrip + current slide + empty note pane).
      *Caption:* "Generate a conference-grade Beamer deck — slides only, real figures, no hallucinated graphics."
- [ ] ★ `12-slides-notes-added.png` — after "generate speaker notes": note pane filled;
      chip now shows "Edit notes".
      *Caption:* "Speaker notes are opt-in — authored separately from the slides."
- [ ] ★ `13-slides-notes-zh-before.png` / `13-slides-notes-zh-after.png` — after
      "把講稿變成繁體中文": note flips to Traditional Chinese while the **slide is unchanged**.
      *Caption:* "Re-language the notes in any language — without regenerating the slides."
- [ ] `14-slides-edit-diff-before.png` / `14-slides-edit-diff-after.png` — "make slide 3
      more concise" → just that slide changes.
      *Caption:* "Diff-edit one slide (or note) by chat — never a full regen."
- [ ] `15-slides-trace.png` — the trace of a slides run (understand → narrate → draft →
      coherence → compile).
      *Caption:* "The Report Agent simulates how a PhD builds a talk — every stage traced."

### Polish
- [ ] `16-math-rendering.png` — an answer with rendered KaTeX equations.
      *Caption:* "LaTeX math renders inline."

---

When captured, a few of these (esp. 04/05, 09, 11–13) are worth embedding in the
top-level `README.md` feature section — ping me and I'll wire them in.
