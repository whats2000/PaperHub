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
): Promise<IngestResult> {
  return apiFetch<IngestResult>("/papers", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, paper_id: paperId }),
  });
}

export async function createBackendSession(): Promise<number> {
  const data = await apiFetch<{ session_id: number }>("/sessions", {
    method: "POST",
  });
  return data.session_id;
}
