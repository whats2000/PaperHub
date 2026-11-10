import i18n from "@/lib/i18n";
import type {
  ReferenceItem,
  LibraryItem,
  IngestResult,
  ChunkResolution,
  ConfirmStatus,
  SessionSummary,
  BackendMessage,
  MemoryItem,
  MemoryStatus,
  MemoryScope,
  DeckMeta,
  DeckSlideDetail,
  ManualEditResult,
  SlideSourceSection,
  ToolCallRecord,
  ForkResult,
  VersionInfo,
  RunEventsResponse,
} from "@/types/domain";
import type {
  ParagraphsResponse,
  TranslateConfig,
  TranslateRequest,
  TranslateResponse,
} from "@/types/translation";
import type {
  ExportPreview,
  ExportRequest,
  ExportResult,
  FeedbackCase,
  FeedbackGuess,
  FeedbackPut,
  FeedbackSummary,
  G1InboxRequest,
  G1InboxResult,
  Preset,
  PresetCreate,
  QueueItem,
  QueueSubmit,
  QueueSubmitResult,
  RunFeedback,
  Stage,
} from "@/types/feedback";
import type {
  DocumentDomainsPut,
  DocumentOut,
  DomainPatch,
  ExploreResponse,
  ExploreStatus,
  FeedbackToG1Response,
  GraphDomain,
  GraphResponse,
  Idea,
  IdeaChatResponse,
  IdeaPatch,
  IdeationFeedbackSummary,
  Problem,
  ProblemCreate,
} from "@/types/ideation";

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function listSessionReferences(
  sessionId: number,
): Promise<ReferenceItem[]> {
  return apiFetch<ReferenceItem[]>(`/papers?session_id=${sessionId}`);
}

export async function toggleReference(
  papersId: number,
  enabled: boolean,
): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>(`/papers/${papersId}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export async function removeReference(papersId: number): Promise<void> {
  await apiFetch<undefined>(`/papers/${papersId}`, { method: "DELETE" });
}

export async function listLibrary(
  sessionId: number,
  q?: string,
  limit?: number,
  offset?: number,
): Promise<LibraryItem[]> {
  const params = new URLSearchParams({ session_id: String(sessionId) });
  if (q) params.set("q", q);
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  return apiFetch<LibraryItem[]>(`/papers/library?${params.toString()}`);
}

export async function attachFromLibrary(
  sessionId: number,
  paperContentId: number,
): Promise<IngestResult> {
  return apiFetch<IngestResult>("/papers/from-library", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      paper_content_id: paperContentId,
    }),
  });
}

export async function ingestPaper(
  sessionId: number,
  paperId: string,
  metadata?: {
    title: string;
    abstract: string | null;
    authors: string[];
    year: number | null;
  },
): Promise<IngestResult> {
  const body: Record<string, unknown> = {
    session_id: sessionId,
    paper_id: paperId,
  };
  if (metadata) {
    body.title = metadata.title;
    body.abstract = metadata.abstract;
    body.authors = metadata.authors;
    body.year = metadata.year;
  }
  return apiFetch<IngestResult>("/papers", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** List backend-of-record sessions (those with ≥1 message), newest activity
 * first. The frontend merges these into the local store on load so sessions
 * are shared across devices, not trapped in one browser's localStorage. */
export async function listSessions(): Promise<SessionSummary[]> {
  return apiFetch<SessionSummary[]>("/sessions");
}

/** Replay a session's persisted message history (chronological). Used to
 * lazily hydrate a session opened from the cross-device list. */
export async function fetchSessionMessages(
  sessionId: number,
): Promise<BackendMessage[]> {
  return apiFetch<BackendMessage[]>(`/sessions/${sessionId}/messages`);
}

export async function createBackendSession(): Promise<number> {
  const data = await apiFetch<{ session_id: number }>("/sessions", {
    method: "POST",
  });
  return data.session_id;
}

/** Delete a backend session. Empty sessions are removed outright; sessions
 * with content are soft-deleted (tombstoned) so they vanish from every device
 * immediately but can be restored. `paper_content` rows always survive —
 * they're deduplicated across sessions. */
export async function deleteBackendSession(sessionId: number): Promise<void> {
  await apiFetch<undefined>(`/sessions/${sessionId}`, { method: "DELETE" });
}

/** Undo a soft delete — clear the tombstone so the session is live again on
 * every device, with its message history intact. */
export async function restoreBackendSession(sessionId: number): Promise<void> {
  await apiFetch<undefined>(`/sessions/${sessionId}/restore`, {
    method: "POST",
  });
}

/** Fork a session at a chosen user message. `runId` is that message's turn
 *  run_id; the backend copies everything strictly above it (messages, enabled
 *  references, session memories, deck) into a new session and returns the
 *  forked message text for the composer prefill. */
export async function forkSession(
  sessionId: number,
  runId: number,
): Promise<ForkResult> {
  return apiFetch<ForkResult>(`/sessions/${sessionId}/fork`, {
    method: "POST",
    body: JSON.stringify({ run_id: runId, ui_language: i18n.resolvedLanguage ?? i18n.language }),
  });
}

/** Custom error thrown by deleteLibraryPaper when the paper is still attached
 * to one or more sessions and force=false. The UI can read `session_count` to
 * compose a confirmation prompt and retry with force=true. */
export class PaperInUseByOtherSessions extends Error {
  readonly session_count: number;
  constructor(session_count: number) {
    super(`paper is referenced by ${session_count} session(s)`);
    this.name = "PaperInUseByOtherSessions";
    this.session_count = session_count;
  }
}

/** Purge a paper from the library entirely — paper_content row + chunks +
 * Chroma vectors + on-disk cache. Destructive; test-friendly endpoint.
 *
 * @throws PaperInUseByOtherSessions on 409 (without force).
 */
export async function deleteLibraryPaper(
  paperContentId: number,
  force = false,
): Promise<void> {
  const url = `/papers/content/${paperContentId}${force ? "?force=true" : ""}`;
  const res = await fetch(`${API_BASE_URL}${url}`, { method: "DELETE" });
  if (res.status === 204) return;
  if (res.status === 409) {
    const body = (await res.json().catch(() => ({}))) as {
      detail?: { session_count?: number };
    };
    throw new PaperInUseByOtherSessions(body.detail?.session_count ?? 0);
  }
  const text = await res.text().catch(() => "");
  throw new Error(`API ${res.status}: ${text}`);
}

/** Resolve a `[chunk:<id>]` citation marker to the paper it lives in and the
 * passage text the Citation Canvas searches for in the rendered HTML. */
export async function getChunk(chunkId: number): Promise<ChunkResolution> {
  return apiFetch<ChunkResolution>(`/chunks/${chunkId}`);
}

/** Which document to show in the Citation Canvas: a PDF-rendered paper's
 *  HTML is broken (PyMuPDF), so the canvas shows the original PDF instead. */
export async function getDocumentMode(
  paperContentId: number,
): Promise<"pdf" | "html"> {
  return (await getDocumentInfo(paperContentId)).mode;
}

export interface DocumentInfo {
  mode: "pdf" | "html";
  /** The paper has a page PDF (its own, or one compiled from its LaTeX):
   *  page-region citations open on it, as a PDF tab for HTML papers. */
  page_pdf: boolean;
}

export async function getDocumentInfo(paperContentId: number): Promise<DocumentInfo> {
  const d = await apiFetch<{ mode: "pdf" | "html"; page_pdf?: boolean }>(
    `/papers/content/${paperContentId}/document`,
  );
  return { mode: d.mode, page_pdf: d.page_pdf === true };
}

/** The page PDF's bytes (what the agent viewed; [view:] citations open on it). */
export async function fetchPagePdfData(paperContentId: number): Promise<Uint8Array> {
  const res = await fetch(`${API_BASE_URL}/papers/content/${paperContentId}/page-pdf`);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

/** A `[view:]` region on the page PDF: 0-based page, box in PDF points. */
export interface ViewRegion {
  page: number;
  bbox: number[];
  source: "own" | "compiled";
}

export async function getViewRegion(
  paperContentId: number,
  page: number,
  box: readonly number[],
): Promise<ViewRegion> {
  const q = new URLSearchParams({ page: String(page), box: box.join(",") });
  return apiFetch<ViewRegion>(`/papers/content/${paperContentId}/view-region?${q}`);
}

/** Fetch a paper's rendered HTML as a string. The Citation Canvas embeds it via
 * an iframe `srcdoc` so the document is SAME-ORIGIN (the app can read its DOM to
 * highlight + theme it) — a cross-origin iframe `src` would block that. */
export async function fetchPaperHtml(paperContentId: number): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/papers/content/${paperContentId}/html`);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.text();
}

/** Fetch a paper's PDF bytes. Passed to react-pdf as `{ data }` so it renders
 * inline from local bytes — no cross-origin iframe, no browser download. */
export async function fetchPaperPdfData(
  paperContentId: number,
): Promise<Uint8Array> {
  const res = await fetch(`${API_BASE_URL}/papers/content/${paperContentId}/pdf`);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

const ARXIV_NEW = /^(\d{4}\.\d{4,5})(v\d+)?$/i;
const ARXIV_OLD = /^([a-z-]+(\.[A-Z]{2})?\/\d{7})(v\d+)?$/i;

/** Normalise user-supplied arXiv input to canonical `arxiv:<id>` form, or
 * null if it doesn't look like an arXiv identifier. Accepts bare IDs,
 * `arxiv:` prefix, and `arxiv.org/abs/` or `arxiv.org/pdf/` URLs, with or
 * without a trailing `vN` version suffix. */
export function parseArxivId(input: string): string | null {
  let s = input.trim();
  if (!s) return null;
  // Strip URL forms first.
  const urlMatch = s.match(
    /arxiv\.org\/(?:abs|pdf)\/([^?#\s]+?)(?:\.pdf)?(?:[?#]|$)/i,
  );
  if (urlMatch && urlMatch[1]) s = urlMatch[1];
  // Strip arxiv: prefix.
  s = s.replace(/^arxiv:/i, "");
  // Match new-style or old-style, capturing without version.
  const mNew = s.match(ARXIV_NEW);
  if (mNew && mNew[1]) return `arxiv:${mNew[1]}`;
  const mOld = s.match(ARXIV_OLD);
  if (mOld && mOld[1]) return `arxiv:${mOld[1]}`;
  return null;
}

/** List all memories visible to a session (active + superseded). A null
 *  sessionId (empty chat with no backend session yet) lists global memories
 *  only. */
export async function listMemories(
  sessionId: number | null,
): Promise<MemoryItem[]> {
  const qs = sessionId === null ? "" : `?session_id=${sessionId}`;
  return apiFetch<MemoryItem[]>(`/memories${qs}`);
}

/** Build the optional ownership header. A null sessionId (no backend session
 *  yet) sends no header, which the backend treats as global-only access. */
function memoryOwnerHeader(sessionId: number | null): Record<string, string> {
  return sessionId === null ? {} : { "X-Paperhub-Session-Id": String(sessionId) };
}

/** Update a memory's content and/or status. The owning session id (when
 *  present) is sent as `X-Paperhub-Session-Id` for ownership verification;
 *  a null id grants global-only access. */
export async function patchMemory(
  memoryId: number,
  patch: { content?: string; status?: MemoryStatus },
  sessionId: number | null,
): Promise<MemoryItem> {
  return apiFetch<MemoryItem>(`/memories/${memoryId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...memoryOwnerHeader(sessionId),
    },
    body: JSON.stringify(patch),
  });
}

/** Hard-delete a memory row. The owning session id (when present) is sent as
 *  `X-Paperhub-Session-Id` for ownership verification; a null id grants
 *  global-only access. */
export async function deleteMemory(
  memoryId: number,
  sessionId: number | null,
): Promise<void> {
  await apiFetch<undefined>(`/memories/${memoryId}`, {
    method: "DELETE",
    headers: memoryOwnerHeader(sessionId),
  });
}

/** Custom error thrown by createMemory when the safety gate refuses the content
 * (HTTP 422). The `reason` field carries the backend's explanation so the UI
 * can show it inline. */
export class MemoryGateRefused extends Error {
  readonly reason: string;
  constructor(reason: string) {
    super(`Memory gate refused: ${reason}`);
    this.name = "MemoryGateRefused";
    this.reason = reason;
  }
}

/** Create a new memory entry for the given session. Scope "session" pins the
 *  memory to this session; "global" applies across all sessions.
 *
 * @throws MemoryGateRefused on HTTP 422 (safety gate rejected the content).
 */
export async function createMemory(
  content: string,
  scope: MemoryScope,
  sessionId: number | null,
): Promise<MemoryItem> {
  const res = await fetch(`${API_BASE_URL}/memories`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...memoryOwnerHeader(sessionId),
    },
    body: JSON.stringify({ content, scope }),
  });
  if (res.status === 422) {
    let reason = "may be sensitive content";
    try {
      const body = (await res.json()) as {
        detail?: string | { msg?: string } | Array<{ msg?: string }>;
      };
      if (typeof body.detail === "string") {
        reason = body.detail;
      } else if (Array.isArray(body.detail)) {
        reason = body.detail.map((d) => d.msg ?? "").join("; ") || reason;
      } else if (body.detail && typeof body.detail === "object" && "msg" in body.detail) {
        reason = body.detail.msg ?? reason;
      }
    } catch {
      // ignore parse error — use default reason
    }
    throw new MemoryGateRefused(reason);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return (await res.json()) as MemoryItem;
}

/** Multipart PDF upload. Backend hashes the bytes → sha256-keyed cache,
 * so re-uploading the same file produces `cache_hit: true`. */
export async function uploadPdf(
  sessionId: number,
  file: File,
  opts?: { private?: boolean },
): Promise<IngestResult> {
  const form = new FormData();
  form.append("session_id", String(sessionId));
  form.append("file", file);
  if (opts?.private) form.append("private", "true");
  const res = await fetch(`${API_BASE_URL}/papers/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return (await res.json()) as IngestResult;
}

/** Fetch deck metadata for a session (GET /sessions/{id}/deck).
 * Throws on non-2xx (including 404 when no deck has been generated yet). */
export async function getDeck(sessionId: number): Promise<DeckMeta> {
  return apiFetch<DeckMeta>(`/sessions/${sessionId}/deck`);
}

/** Deck metadata for a session, or null when it has none
 * (GET /sessions/{id}/deck?optional=1). For probes on chats that usually
 * have no deck: a 404 would log a browser console error for that normal case. */
export async function getDeckIfAny(sessionId: number): Promise<DeckMeta | null> {
  return apiFetch<DeckMeta | null>(`/sessions/${sessionId}/deck?optional=1`);
}

/** Manually set the speaker note for the slide occupying `page`. Returns the
 *  rebuilt page→note map (the backend resolves a continuation page to its
 *  owning slide). */
export async function updateDeckNote(
  sessionId: number,
  page: number,
  text: string,
): Promise<{ speaker_notes: Record<string, string>; has_notes: boolean }> {
  return apiFetch<{ speaker_notes: Record<string, string>; has_notes: boolean }>(
    `/sessions/${sessionId}/deck/notes/${page}`,
    { method: "PATCH", body: JSON.stringify({ text }) },
  );
}

/** Build the URL for streaming the session's compiled deck PDF directly from
 * the backend. Use `fetchDeckPdfData` to load the bytes for react-pdf. */
export function deckPdfUrl(sessionId: number): string {
  return `${API_BASE_URL}/sessions/${sessionId}/deck/pdf`;
}

/** Like `deckPdfUrl` but pins the download to a specific version's cached PDF. */
export function deckPdfUrlForVersion(sessionId: number, versionId: string | null): string {
  return versionId
    ? `${API_BASE_URL}/sessions/${sessionId}/deck/pdf?version_id=${encodeURIComponent(versionId)}`
    : deckPdfUrl(sessionId);
}

/** Fetch a session's compiled deck PDF bytes. Passed to react-pdf as
 * `{ data }` so it renders inline — no cross-origin iframe needed.
 *
 * `version` is an optional cache-bust token: the deck URL is stable across
 * recompiles (one deck per session), so an edit produces new bytes at the same
 * URL. Bumping `?v=` forces the browser past its HTTP cache to the freshly
 * compiled PDF. */
export async function fetchDeckPdfData(
  sessionId: number,
  version?: number,
): Promise<Uint8Array> {
  const base = deckPdfUrl(sessionId);
  const url = version === undefined ? base : `${base}?v=${version}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

/** Build the URL for downloading the session's deck LaTeX source. */
export function deckTexUrl(sessionId: number): string {
  return `${API_BASE_URL}/sessions/${sessionId}/deck/tex`;
}

/** Fetch the deck LaTeX source as TEXT (for the manual whole-deck editor) —
 *  distinct from `deckTexUrl`, which is a download link. */
export async function getDeckTexText(sessionId: number): Promise<string> {
  const res = await fetch(deckTexUrl(sessionId));
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.text();
}

/** Fetch per-slide detail (frame source + source grounding) for the manual
 *  frame editor + the per-page Sources strip (GET /deck/slides). */
export async function getDeckSlides(
  sessionId: number,
): Promise<DeckSlideDetail[]> {
  return apiFetch<DeckSlideDetail[]>(`/sessions/${sessionId}/deck/slides`);
}

/** Manually edit the frame occupying `page` and recompile the whole deck.
 *  A compile failure resolves to `{ok:false, log}` (HTTP 200), NOT a throw —
 *  the editor renders the log and keeps the last-good PDF on screen. */
export async function putFrameTex(
  sessionId: number,
  page: number,
  frameTex: string,
): Promise<ManualEditResult> {
  return apiFetch<ManualEditResult>(
    `/sessions/${sessionId}/deck/slides/${page}/tex`,
    { method: "PUT", body: JSON.stringify({ frame_tex: frameTex }) },
  );
}

/** Manually replace the entire deck source and recompile. Same `{ok:false,log}`
 *  contract on a compile failure as `putFrameTex`. */
export async function putDeckTex(
  sessionId: number,
  tex: string,
): Promise<ManualEditResult> {
  return apiFetch<ManualEditResult>(`/sessions/${sessionId}/deck/tex`, {
    method: "PUT",
    body: JSON.stringify({ tex }),
  });
}

/** The section names of a paper, in document order — the deterministic picker
 *  list for the per-slide Sources editor. */
export async function getPaperSections(
  paperContentId: number,
): Promise<string[]> {
  return apiFetch<string[]>(`/papers/content/${paperContentId}/sections`);
}

/** Set a slide's grounding from the structured Sources editor — deterministic,
 *  NO recompile (the `% cite:` marker is a LaTeX comment). Returns the resolved
 *  source sections (with chunk_ids). */
export async function putSlideSources(
  sessionId: number,
  page: number,
  sources: { paper_id: number; section_name: string }[],
): Promise<{ ok: boolean; source_sections: SlideSourceSection[] }> {
  return apiFetch<{ ok: boolean; source_sections: SlideSourceSection[] }>(
    `/sessions/${sessionId}/deck/slides/${page}/sources`,
    { method: "PUT", body: JSON.stringify({ sources }) },
  );
}

/** Fetch a run's recorded agent trace (tool_calls), lazily, after a refresh
 * has dropped the streamed trace. Returns the same shape the Trace panel
 * renders. Throws on non-2xx (e.g. 404 when the run isn't in the session). */
export async function fetchRunTrace(
  sessionId: number,
  runId: number,
): Promise<ToolCallRecord[]> {
  return apiFetch<ToolCallRecord[]>(
    `/sessions/${sessionId}/runs/${runId}/trace`,
  );
}

// ── Settings (Plan G / FR-14) ────────────────────────────────────────────────

export interface SettingsField {
  key: string;
  label: string;
  type: "string" | "int" | "bool" | "email" | "enum" | "secret";
  value?: string | null;
  is_set?: boolean;
  is_default?: boolean;
  secret: boolean;
  restart_required: boolean;
  read_only?: boolean;
  help?: string;
  /** Seldom-configured (e.g. per-slot model overrides); shown under a
   * collapsed disclosure in the modal rather than inline. */
  advanced?: boolean;
  choices?: string[];
  min?: number;
  max?: number;
  /** Optional "where to get this" link (e.g. a provider's API-key page). */
  docs_url?: string;
  /** Optional sub-group key within a category; the panel renders a heading per
   *  contiguous group. */
  group?: string;
}

export interface SettingsCredentials {
  suggestions: string[];
  keys: { key: string; is_set?: boolean }[];
}

export interface SettingsCategory {
  key: string;
  label: string;
  credentials?: SettingsCredentials;
  fields: SettingsField[];
}

export interface SettingsConfig {
  categories: SettingsCategory[];
}

export interface SettingsPatchResult {
  updated: string[];
  cleared: string[];
  restart_required: string[];
}

/** Fetch the full settings registry grouped by category. Secrets are masked
 * (no `value` field; only `is_set`). */
export async function getSettings(): Promise<SettingsConfig> {
  return apiFetch<SettingsConfig>("/settings");
}

/** Apply a partial settings patch. Keys mapped to `null` clear the override
 * (reverts to `.env` / built-in default). Returns which keys were updated,
 * cleared, and which require a backend restart to take effect. */
export async function patchSettings(
  patch: Record<string, string | null>,
): Promise<SettingsPatchResult> {
  return apiFetch<SettingsPatchResult>("/settings", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export interface SettingsReadiness {
  /** True iff both gate models are runnable right now (composer unlock). */
  ready: boolean;
  /** Whether ≥1 provider credential is set. */
  credentials_set: boolean;
  models: {
    small: SettingsModelCheck;
    flagship: SettingsModelCheck;
    /** The core agent model (PAPERHUB_AGENT_MODEL, else the flagship),
     *  pre-flighted with a tools list: it must support tool calling. */
    agent?: SettingsModelCheck;
  };
}

export interface SettingsModelCheck {
  model: string;
  /** The model is runnable: key present AND a 1-token pre-flight succeeded. */
  key_ok: boolean;
  /** Env var names LiteLLM still needs for this model's provider. */
  missing_keys: string[];
  /** Exception class name when the pre-flight failed (e.g. AuthenticationError). */
  error?: string | null;
  /** The provider's own first-line reason (redacted) — tells the user whether
   *  it's the key or the model that's wrong. */
  detail?: string | null;
}

/** First-run gate: are the small + flagship models usable? Drives the composer
 * lock + onboarding tour. Cheap (no network on the backend); safe on boot. */
export async function getReadiness(): Promise<SettingsReadiness> {
  return apiFetch<SettingsReadiness>("/settings/readiness");
}

export interface SettingsModelOptions {
  /** Providers derived from the set credential keys. */
  providers: string[];
  /** Usable model ids per provider (live-discovered or static fallback). */
  options: Record<string, string[]>;
}

/** Autocomplete suggestions for the model-name fields, per configured provider.
 * Best-effort — the model name stays free text, so this never blocks. */
export async function getModelOptions(): Promise<SettingsModelOptions> {
  return apiFetch<SettingsModelOptions>("/settings/model-options");
}

// ── Run cancel + event replay (FR-15) ────────────────────────────────────────

/** Fire-and-forget cancel for an in-flight run. Safe to call after the stream
 *  is already done — the backend no-ops on a finished run. */
/** Stop a run. `kept`: the turn had already acted (a paper added, a memory
 *  saved), so the backend keeps its question and a stopped stub. */
export async function cancelRun(runId: number): Promise<{ kept: boolean }> {
  const res = await apiFetch<{ status: string; kept?: boolean } | undefined>(
    `/chat/cancel`,
    { method: "POST", body: JSON.stringify({ run_id: runId }) },
  );
  return { kept: res?.kept === true };
}

/** Poll for recorded SSE events after a reconnect (A10 reattach path).
 *  `since` is the last `next_cursor` value (0 on first call). */
export async function fetchRunEvents(
  runId: number,
  since: number,
): Promise<RunEventsResponse> {
  return apiFetch<RunEventsResponse>(`/chat/runs/${runId}/events?since=${since}`);
}

// ── Agent actions (H4) ───────────────────────────────────────────────────────

/** A confirm card's action is no longer pending (already confirmed,
 *  dismissed, or running elsewhere): the endpoints answer 409. */
export class ActionNotPending extends Error {
  constructor() {
    super("action is not pending");
    this.name = "ActionNotPending";
  }
}

export type ConfirmActionResult =
  | { status: "done"; result: { content: string; record?: Record<string, unknown> } }
  | { status: "failed"; error: string };

async function postAction<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { method: "POST" });
  if (res.status === 409) throw new ActionNotPending();
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

/** Run a pending confirm-tier action directly (no LLM). */
export async function confirmAction(actionId: string): Promise<ConfirmActionResult> {
  return postAction<ConfirmActionResult>(
    `/chat/actions/${encodeURIComponent(actionId)}/confirm`,
  );
}

/** A confirm-tier action's current state (re-read after a 409). */
export async function getAction(actionId: string): Promise<{
  status: ConfirmStatus;
  result?: { content: string; record?: Record<string, unknown> } | null;
  error?: string;
}> {
  return apiFetch(`/chat/actions/${encodeURIComponent(actionId)}`);
}

/** Dismiss a pending confirm-tier action. */
export async function dismissAction(actionId: string): Promise<void> {
  await postAction<{ status: string }>(
    `/chat/actions/${encodeURIComponent(actionId)}/dismiss`,
  );
}

/** Turn the per-chat agent auto-approve switch on or off. */
export async function setSessionAutoApprove(
  sessionId: number,
  autoApprove: boolean,
): Promise<{ id: number; auto_approve: boolean }> {
  return apiFetch<{ id: number; auto_approve: boolean }>(`/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ auto_approve: autoApprove }),
  });
}

// ── Version (FR-16) ──────────────────────────────────────────────────────────

/** Fetch the running backend version and update-availability status. */
export async function getVersion(): Promise<VersionInfo> {
  return apiFetch<VersionInfo>("/version");
}

// ── Bilingual reading (Plan H1) ──────────────────────────────────────────────

/** Translate 1..5 adjacent blocks (placeholders already substituted). */
export async function translateBlocks(
  req: TranslateRequest,
): Promise<TranslateResponse> {
  return apiFetch<TranslateResponse>("/translations", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/** Server-side translation switches (e.g. whether the full paper is translated). */
export async function getTranslateConfig(): Promise<TranslateConfig> {
  return apiFetch<TranslateConfig>("/translations/config");
}

/** PDF paragraphs (text + math + page geometry) for a paper's bilingual view. */
export async function getPdfParagraphs(
  paperContentId: number,
): Promise<ParagraphsResponse> {
  return apiFetch<ParagraphsResponse>(
    `/papers/content/${paperContentId}/paragraphs`,
  );
}

// ── Feedback flywheel (Plan H2) ──────────────────────────────────────────────

/** Save (or clear, with `polarity: null`) the explicit feedback on one answer. */
export async function putRunFeedback(
  runId: number,
  body: FeedbackPut,
): Promise<void> {
  await apiFetch<unknown>(`/feedback/runs/${runId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Reasons the run's trace signals suggest, used to pre-select the chips. */
export async function getRunFeedbackGuess(runId: number): Promise<FeedbackGuess> {
  return apiFetch<FeedbackGuess>(`/feedback/runs/${runId}/guess`);
}

/** How many feedback rows (thumbs + reviewer rows) hang off a chat's runs. */
export async function getSessionFeedbackCount(sessionId: number): Promise<number> {
  const r = await apiFetch<{ count: number }>(`/feedback/sessions/${sessionId}/count`);
  return r.count;
}

/** Every explicit feedback row of a session, to restore the thumbs on reload. */
export async function listSessionFeedback(sessionId: number): Promise<RunFeedback[]> {
  return apiFetch<RunFeedback[]>(`/feedback/runs?session_id=${sessionId}`);
}

/** Reasons promoted from "other" text into presets. */
export async function listFeedbackPresets(): Promise<Preset[]> {
  return apiFetch<Preset[]>("/feedback/presets");
}

/** Implicit feedback the reviewer was unsure about, with full conversation context. */
export async function getFeedbackQueue(): Promise<QueueItem[]> {
  return apiFetch<QueueItem[]>("/feedback/queue");
}

/** Finalise the answered queue items; unanswered ones stay queued. */
export async function submitFeedbackQueue(body: QueueSubmit): Promise<QueueSubmitResult> {
  return apiFetch<QueueSubmitResult>("/feedback/queue/submit", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Aggregates for the Feedback page: KPIs, per-stage counts, "other" texts. */
export async function getFeedbackSummary(): Promise<FeedbackSummary> {
  return apiFetch<FeedbackSummary>("/feedback/summary");
}

/** The negative-feedback cases attributed to one stage. */
export async function getFeedbackCases(stage: Stage): Promise<FeedbackCase[]> {
  return apiFetch<FeedbackCase[]>(`/feedback/cases?stage=${encodeURIComponent(stage)}`);
}

/** Promote a recurring "other" text to a preset reason under a stage. */
export async function postFeedbackPreset(body: PresetCreate): Promise<{ label: string }> {
  return apiFetch<{ label: string }>("/feedback/presets", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Append feedback cases to the G1 to-label inbox. */
export async function postG1Inbox(body: G1InboxRequest): Promise<G1InboxResult> {
  return apiFetch<G1InboxResult>("/feedback/g1-inbox", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function exportQuery(opts: ExportRequest): string {
  const q = new URLSearchParams();
  q.set("include_implicit", String(opts.include_implicit ?? false));
  q.set("include_private", String(opts.include_private ?? true));
  q.set("cited_only", String(opts.cited_only ?? false));
  if (opts.since) q.set("since", opts.since);
  if (opts.until) q.set("until", opts.until);
  if (opts.model) q.set("model", opts.model);
  return q.toString();
}

/** The counts an export with these options would write; writes nothing. */
export async function getExportPreview(opts: ExportRequest): Promise<ExportPreview> {
  return apiFetch<ExportPreview>(`/feedback/export/preview?${exportQuery(opts)}`);
}

/** Write the Hugging Face SFT/DPO files under the backend's exports dir. */
export async function postExport(body: ExportRequest): Promise<ExportResult> {
  return apiFetch<ExportResult>("/feedback/export", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Download URL of one exported file. */
export function exportFileUrl(dir: string, file: string): string {
  return `${API_BASE_URL}/feedback/exports/${encodeURIComponent(dir)}/${encodeURIComponent(file)}`;
}

// ---- Ideation (Plan H3) ----

/** The ideation graph of one scope: a problem's ideas, or free exploration. */
export async function getIdeationGraph(scope: "explore" | number): Promise<GraphResponse> {
  const q = scope === "explore" ? "explore=1" : `problem_id=${scope}`;
  return apiFetch<GraphResponse>(`/ideation/graph?${q}`);
}

export async function listIdeationProblems(): Promise<Problem[]> {
  return apiFetch<Problem[]>("/ideation/problems");
}

/** Create a problem; the backend starts generating its ideas in the background. */
export async function createIdeationProblem(body: ProblemCreate): Promise<Problem> {
  return apiFetch<Problem>("/ideation/problems", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getIdeationProblem(problemId: number): Promise<Problem> {
  return apiFetch<Problem>(`/ideation/problems/${problemId}`);
}

/** Re-run a failed problem's generation (409 unless it failed). */
export async function retryIdeationProblem(problemId: number): Promise<Problem> {
  return apiFetch<Problem>(`/ideation/problems/${problemId}/retry`, { method: "POST" });
}

export async function patchIdeationIdea(ideaId: number, body: IdeaPatch): Promise<Idea> {
  return apiFetch<Idea>(`/ideation/ideas/${ideaId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** Start a free-exploration run in the background (a second call joins the running one). */
export async function postIdeationExplore(language?: string): Promise<ExploreResponse> {
  // Free exploration has no problem text to take a language from: send the UI's.
  return apiFetch<ExploreResponse>("/ideation/explore", {
    method: "POST",
    ...(language ? { body: JSON.stringify({ language }) } : {}),
  });
}

/** Whether a free-exploration run is in flight, and how the latest one ended. */
export async function getIdeationExploreStatus(): Promise<ExploreStatus> {
  return apiFetch<ExploreStatus>("/ideation/explore/status");
}

/** Restore a dismissed idea to the status it had before (prev_status). */
export async function undoDismissIdeationIdea(ideaId: number): Promise<Idea> {
  return apiFetch<Idea>(`/ideation/ideas/${ideaId}/undo-dismiss`, { method: "POST" });
}

/** Open a new chat session linked to the idea, with both documents attached. */
export async function postIdeationIdeaChat(ideaId: number): Promise<IdeaChatResponse> {
  return apiFetch<IdeaChatResponse>(`/ideation/ideas/${ideaId}/chat`, { method: "POST" });
}

/** Manually set a document's domains; re-analysis never overwrites the edit. */
export async function putIdeationDocumentDomains(
  docId: number,
  body: DocumentDomainsPut,
): Promise<DocumentOut> {
  return apiFetch<DocumentOut>(`/ideation/documents/${docId}/domains`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Rename a domain or merge it into another; returns the domain list. */
export async function patchIdeationDomain(domainId: number, body: DomainPatch): Promise<GraphDomain[]> {
  return apiFetch<GraphDomain[]>(`/ideation/domains/${domainId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function getIdeationFeedbackSummary(): Promise<IdeationFeedbackSummary> {
  return apiFetch<IdeationFeedbackSummary>("/ideation/feedback-summary");
}

/** Append idea outcomes to the ideation eval data (the G1 inbox). */
export async function postIdeationFeedbackToG1(): Promise<FeedbackToG1Response> {
  return apiFetch<FeedbackToG1Response>("/ideation/feedback-to-g1", { method: "POST" });
}

/** A figure a chat answer cites as `[fig:P/ID]` (H4 figure citations). */
export interface FigureMeta {
  paper_content_id: number;
  id: string;
  caption: string;
  /** 0-based page on the paper's page PDF. */
  page: number | null;
  title: string;
  has_image: boolean;
  /** The HTML anchor (`phfig-NNN`) the render put before the figure. */
  html_anchor: string | null;
  /** The figure's box on its page, in PDF points (top-left origin). */
  bbox: number[] | null;
}

export async function getFigureMeta(paperContentId: number, figureId: string): Promise<FigureMeta> {
  return apiFetch<FigureMeta>(
    `/papers/content/${paperContentId}/figures/${encodeURIComponent(figureId)}`,
  );
}

export function figureImageUrl(paperContentId: number, figureId: string): string {
  return `${API_BASE_URL}/papers/content/${paperContentId}/figures/${encodeURIComponent(figureId)}/image`;
}
