export type Intent =
  | "paper_search"
  | "paper_suggest"
  | "paper_qa"
  | "slides"
  | "library_stats"
  | "memory"
  | "clarify"
  | "chitchat";

export type ModelTier = "small" | "flagship";
export type ToolStatus = "ok" | "error" | "rejected";
export type Branch = "" | "A" | "B";

export interface RoutingDecision {
  intent: Intent;
  model_tier: ModelTier;
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
}

export interface LibraryItem {
  paper_content_id: number;
  arxiv_id: string | null;
  title: string;
  abstract: string | null;
  year: number | null;
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

export type IngestResult = AttachResult;

export interface DeckMeta {
  deck_id: number;
  session_id: number;
  page_count: number;
  theme: string;
  status: "ok" | "error";
  plan: unknown;
  speaker_notes: Record<string, string>;
  contributing_paper_ids: number[];
  updated_at: string;
}

export interface DeckEventData {
  deck_id: number;
  session_id: number;
  page_count: number;
  title: string;
  status: "ok" | "error";
  contributing_papers: { id: number; title: string }[];
  has_notes: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  run_id: number | null;
  routing_decision?: RoutingDecision;
  trace?: ToolCallRecord[];
  status?: "streaming" | "ok" | "error";
  error?: string;
  search_results?: SearchResultCandidate[];
  deck?: DeckEventData;
}

export interface ChatSession {
  id: number;
  title: string;
  messages: ChatMessage[];
  backend_session_id: number | null;
}

/** Backend-of-record session row (GET /sessions). The frontend merges these
 *  into the local store on load so sessions are shared across devices. */
export interface SessionSummary {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

/** One persisted message replayed from GET /sessions/{id}/messages. */
export interface BackendMessage {
  role: "user" | "assistant" | "system";
  content: string;
  run_id: number | null;
  created_at: string;
  routing_decision?: RoutingDecision;
  /** Paper-search result cards emitted on this turn, replayed so they show on
   *  every device (null for non-search turns). */
  search_results?: SearchResultCandidate[] | null;
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
