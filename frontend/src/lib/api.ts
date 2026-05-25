import type {
  ReferenceItem,
  LibraryItem,
  AttachResult,
  IngestResult,
  ChunkResolution,
  SessionSummary,
  BackendMessage,
  MemoryItem,
  MemoryStatus,
  MemoryScope,
  DeckMeta,
  ToolCallRecord,
} from "@/types/domain";

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
): Promise<AttachResult> {
  return apiFetch<AttachResult>("/papers/from-library", {
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
  const d = await apiFetch<{ mode: "pdf" | "html" }>(
    `/papers/content/${paperContentId}/document`,
  );
  return d.mode;
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
): Promise<IngestResult> {
  const form = new FormData();
  form.append("session_id", String(sessionId));
  form.append("file", file);
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

/** Build the URL for streaming the session's compiled deck PDF directly from
 * the backend. Use `fetchDeckPdfData` to load the bytes for react-pdf. */
export function deckPdfUrl(sessionId: number): string {
  return `${API_BASE_URL}/sessions/${sessionId}/deck/pdf`;
}

/** Fetch a session's compiled deck PDF bytes. Passed to react-pdf as
 * `{ data }` so it renders inline — no cross-origin iframe needed. */
export async function fetchDeckPdfData(sessionId: number): Promise<Uint8Array> {
  const res = await fetch(deckPdfUrl(sessionId));
  if (!res.ok) throw new Error(`API ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

/** Build the URL for downloading the session's deck LaTeX source. */
export function deckTexUrl(sessionId: number): string {
  return `${API_BASE_URL}/sessions/${sessionId}/deck/tex`;
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
