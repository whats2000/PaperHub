export type Intent =
  | "paper_search"
  | "paper_suggest"
  | "paper_qa"
  | "slides"
  | "library_stats"
  | "memory"
  | "clarify"
  | "chitchat";

export type ToolStatus = "ok" | "error" | "rejected";
export type Branch = "" | "A" | "B";

export interface RoutingDecision {
  intent: Intent;
  confidence: number;
  reasoning: string;
}

export interface ToolCallRecord {
  run_id: number;
  branch: Branch;
  step_index: number;
  parent_step: number | null;
  agent: string;
  tool: string;
  model: string | null;
  args_redacted_json: Record<string, unknown> | null;
  result_summary_json: Record<string, unknown> | null;
  latency_ms: number;
  token_in: number | null;
  token_out: number | null;
  status: ToolStatus;
  error: string | null;
}

// Stub shape — v2.4-5 will populate from the search_results SSE event.
// All fields per spec so v2.4-5 doesn't need to retype.
export interface SearchResultCandidate {
  paper_id: string; // "arxiv:<id>" | "ss:<paperId>" | "library:<id>"
  title: string;
  authors: string[];
  year: number | null;
  abstract: string | null;
  arxiv_id: string | null;
  has_open_pdf: boolean;
  reason: string;
  finalize: boolean; // v2.4-5: agent's finalize flag
  auto_added: boolean; // v2.4-5: chat endpoint auto-attached this
  papers_id: number | null; // v2.4-5: populated when auto_added=true
  error: string | null; // v2.4-5: "no_ingestible_source" etc.
  /** OA URLs that were tried but all failed (e.g. Cloudflare-blocked bioRxiv).
   *  Present only when error="no_ingestible_source" and Unpaywall was attempted.
   *  Optional so legacy persisted cards without this field still parse cleanly. */
  tried_urls?: string[];
  already_in_session: boolean; // v2.4-5: set by chat layer
}

export interface ReferenceItem {
  papers_id: number;
  paper_content_id: number;
  enabled: boolean;
  added_at: string;
  arxiv_id: string | null;
  title: string;
  year: number | null;
  kind: string; // 'arxiv' | 'pdf_upload' | 'latex_upload'
  /** Confidential document (H0): shown with a lock; never inferred. */
  is_private: boolean;
}

export interface LibraryItem {
  paper_content_id: number;
  arxiv_id: string | null;
  title: string;
  abstract: string | null;
  year: number | null;
  is_private: boolean;
}

export interface ChunkResolution {
  id: number;
  paper_content_id: number;
  section: string | null;
  text: string;
  /** Deterministic anchor (`<span id>`) injected at the chunk's start during
   *  ingest, when its sentinel survived rendering; null → use text-search. */
  dom_id: string | null;
  /** Clean, markdown-stripped text for locating the chunk in the PDF/HTML text
   *  layer. Present for Marker-ingested chunks (Plan F2+); null/absent for older
   *  chunks — callers fall back to `text`. */
  match_text?: string | null;
  /** Marker block provenance (F2.1 A2'): 0-based ABSOLUTE page index the chunk
   *  was extracted from. Drives the exact geometric PDF highlight together with
   *  `bbox`. Null for non-Marker (LaTeX / PyMuPDF) chunks. */
  page?: number | null;
  /** Marker union bbox `[x0,y0,x1,y1]` in PDF points, origin top-left, in the
   *  page's native coordinate space. When present (with `page`), the Citation
   *  Canvas draws the PDF highlight from this geometry instead of text-searching.
   *  Null for non-Marker chunks. */
  bbox?: number[] | null;
}

export interface AttachResult {
  papers_id: number;
  paper_content_id: number;
  cache_hit: boolean;
  title: string;
}

/** Upload/ingest response; `is_private` is the server's truth (the flag is
 *  upgrade-only, so a re-upload of a private PDF stays private). */
export type IngestResult = AttachResult & { is_private: boolean };

export interface DeckMeta {
  deck_id: number;
  session_id: number;
  page_count: number;
  /** F4.5: dropped from the backend schema; left for backwards compat with
   *  pre-F4.5 responses. Always undefined on a fresh F4.5+ backend. */
  theme?: string;
  status: "ok" | "error";
  plan: unknown;
  speaker_notes: Record<string, string>;
  contributing_paper_ids: number[];
  /** F4.5: the version snapshot the SlidesPanel is rendering right now. */
  current_version_id?: string | null;
  updated_at: string;
}

export interface DeckEventData {
  deck_id: number;
  session_id: number;
  page_count: number;
  title: string;
  status: "ok" | "error";
  /** On a live SSE event each entry carries `title`; on message replay the
   *  backend emits `{id}` only, so `title` is optional. */
  contributing_papers: { id: number; title?: string }[];
  has_notes: boolean;
  /** F4.5: the version snapshot THIS turn stamped (null on legacy rows
   *  that pre-date `runs.deck_version_id`). Drives the per-turn DeckChip's
   *  "Switch to this version" affordance — older cards restore via this id. */
  version_id?: string | null;
}

/** One cited source on a slide (F6.2 grounding): the chunks of a paper section
 *  the slide was written from. Empty `chunk_ids` = an "unsourced" cite (the
 *  marker named a section with no evidence). */
export interface SlideSourceSection {
  paper_id: number;
  section_name: string;
  chunk_ids: number[];
}

/** Per-slide detail for the manual frame editor + the Sources strip
 *  (GET /sessions/{id}/deck/slides). */
export interface DeckSlideDetail {
  slide_index: number;
  page_start: number;
  page_end: number;
  /** The bare frame body (with any cite marker). */
  frame_tex: string;
  /** What the LaTeX editor loads: the frame with `% cite:` markers STRIPPED —
   *  the editor is content-only; grounding is managed via the Sources editor. */
  content_tex: string;
  source_sections: SlideSourceSection[];
}

/** Result of a manual recompile (PUT /deck/slides/{page}/tex or /deck/tex). A
 *  compile failure is a normal outcome: `ok:false` with the pdflatex `log`. */
export interface ManualEditResult {
  ok: boolean;
  status: string;
  page_count?: number;
  log?: string;
}

/** The turn summary an agent run shows instead of the routing badge
 *  (SRS §III-10.4): the `agent_summary` SSE event / replayed field. */
export interface AgentSummary {
  papers_read: number;
  steps: number;
  tools: string[];
}

/** A chip's Undo state: `undoable` comes from the replay (false when the
 *  row is gone or belongs to another chat); `undone` is set after Undo. */
interface UndoState {
  undoable?: boolean;
  undone?: boolean;
}

export interface PaperAddedData extends UndoState {
  papers_id: number;
  paper_content_id: number;
  title: string;
}

export interface MemorySavedData extends UndoState {
  memory_id: number;
  content: string;
  scope: "session" | "global";
}

/** pending → (running) → done | failed | dismissed; `unavailable` = the
 *  action belongs to another chat (a fork's copy) or was already handled. */
export type ConfirmStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "dismissed"
  | "unavailable";

/** What a confirm-tier action acts on, looked up by the backend so the card
 *  can name it in words (H4 review #1/#4). */
export type ConfirmTarget =
  | { kind: "paper"; id: number; title: string }
  | { kind: "memory"; id: number; content: string; scope: "session" | "global" }
  | { kind: "other"; summary: string };

export interface ConfirmRequestData {
  action_id: string;
  tool: string;
  /** The target as one line (fallback when `target` is absent). */
  args_summary: string;
  target?: ConfirmTarget;
  /** The backend's (English) reason; shown only via `reason_kind`. */
  reason: string;
  /** Why the user is asked: auto-approve off, the classifier unsure, or the
   *  classifier failed. */
  reason_kind?: "required" | "classifier" | "classifier_failed";
  status?: ConfirmStatus;
  result?: { content: string; record?: Record<string, unknown> } | null;
  error?: string;
}

/** A tool UI event of an agent turn, live (SSE) or replayed (agent_events). */
export type AgentUiEvent =
  | { event: "paper_added"; data: PaperAddedData }
  | { event: "memory_saved"; data: MemorySavedData }
  | { event: "confirm_request"; data: ConfirmRequestData };

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  run_id: number | null;
  routing_decision?: RoutingDecision;
  /** Agent turns (H4): shown instead of the routing badge. */
  agent_summary?: AgentSummary;
  /** Agent turns: paper/memory chips and confirmation cards, in order. */
  agent_events?: AgentUiEvent[];
  /** One card list per search an agent turn ran (`search_results` keeps
   *  every card flattened). */
  search_result_batches?: SearchResultCandidate[][];
  trace?: ToolCallRecord[];
  /** "stopped": the user pressed Stop after the turn had already acted (a
   *  paper added, a memory saved); the stub keeps those chips and Undo. */
  status?: "streaming" | "processing" | "ok" | "error" | "interrupted" | "stopped";
  /** Set while a retracted (stopped) turn animates out before removal. */
  exiting?: boolean;
  /** Stop pressed; waiting for the backend to say whether the turn had
   *  already acted (kept as a stub) or is retracted. */
  stopping?: boolean;
  error?: string;
  search_results?: SearchResultCandidate[];
  deck?: DeckEventData;
}

export interface ChatSession {
  id: number;
  title: string;
  messages: ChatMessage[];
  backend_session_id: number | null;
  /** Backend id of the session this one was forked FROM (SRS v2.30), or null
   *  for a normal session. Drives the sidebar's indented fork grouping. */
  forked_from_session_id?: number | null;
  /** The idea this chat was opened from (Plan H3), or null. Drives the
   *  sidebar's Lightbulb marker. */
  idea_id?: number | null;
  /** Per-chat agent auto-approve switch (H4); off by default. */
  auto_approve?: boolean;
}

/** Backend-of-record session row (GET /sessions). The frontend merges these
 *  into the local store on load so sessions are shared across devices. */
export interface SessionSummary {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  /** Backend id of the parent session for a fork (SRS v2.30), else null. */
  forked_from_session_id?: number | null;
  /** The idea this chat was opened from (Plan H3 idea_sessions), else null. */
  idea_id?: number | null;
  /** Per-chat agent auto-approve switch (H4). */
  auto_approve?: boolean;
}

/** Result of POST /sessions/{id}/fork — the new session + the forked message
 *  text to prefill into the composer (editable, not auto-sent). */
export interface ForkResult {
  session_id: number;
  forked_message: string;
  title: string;
}

/** One persisted message replayed from GET /sessions/{id}/messages. */
export interface BackendMessage {
  role: "user" | "assistant" | "system";
  content: string;
  run_id: number | null;
  created_at: string;
  routing_decision?: RoutingDecision;
  /** Agent runs (H4) replay their turn summary instead of a routing badge. */
  agent_summary?: AgentSummary | null;
  /** Agent runs: chips + confirm cards, each card with its action's status. */
  agent_events?: AgentUiEvent[] | null;
  /** Agent runs: one card list per search. */
  search_result_batches?: SearchResultCandidate[][] | null;
  /** Paper-search result cards emitted on this turn, replayed so they show on
   *  every device (null for non-search turns). */
  search_results?: SearchResultCandidate[] | null;
  /** The slide deck generated on this turn, shaped like the `deck` SSE event,
   *  replayed so the in-chat DeckChip survives a refresh (null for non-slide
   *  turns). On replay `contributing_papers` entries are `{id}` only. */
  deck?: DeckEventData | null;
  /** A10: The run's terminal status (or "running" if still in progress).
   *  Drives interrupted/error status mapping on hydrate + processing placeholder. */
  run_status?: string | null;
}

export type MemoryStatus = "active" | "superseded";
export type MemoryScope = "session" | "global";

export interface MemoryItem {
  id: number;
  scope: MemoryScope;
  session_id: number | null;
  content: string;
  created_at: string;
  updated_at: string;
  status: MemoryStatus;
  supersedes: number | null;
  superseded_by: number | null;
}

/** One in-app changelog entry (FR-16). `highlights` is keyed by locale; the
 *  loader falls back to `en` for any locale missing an entry. */
export interface ChangelogEntry {
  version: string;
  date: string;
  highlights: Record<string, string[]>;
}

/** GET /version payload (FR-16). */
export interface VersionInfo {
  current: string;
  latest: string | null;
  update_available: boolean;
  html_url: string | null;
  checked_at: string | null;
}

/** GET /chat/runs/{id}/events?since={cursor} payload (FR-15). */
export interface RunEventsResponse {
  status: string;
  events: { event: string; data: string }[];
  next_cursor: number;
}
