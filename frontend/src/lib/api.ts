import type {
  ReferenceItem,
  LibraryItem,
  AttachResult,
  IngestResult,
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

export async function createBackendSession(): Promise<number> {
  const data = await apiFetch<{ session_id: number }>("/sessions", {
    method: "POST",
  });
  return data.session_id;
}

/** Delete a backend session and everything tied to it (membership rows,
 * messages, runs, tool_calls). `paper_content` rows survive — they're
 * deduplicated across sessions. */
export async function deleteBackendSession(sessionId: number): Promise<void> {
  await apiFetch<undefined>(`/sessions/${sessionId}`, { method: "DELETE" });
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
