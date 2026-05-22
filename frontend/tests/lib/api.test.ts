import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import {
  parseArxivId,
  getChunk,
  listMemories,
  patchMemory,
  deleteMemory,
  createMemory,
  MemoryGateRefused,
  API_BASE_URL,
} from "@/lib/api";
import type { MemoryItem } from "@/types/domain";

describe("parseArxivId", () => {
  it("accepts a bare new-style ID", () => {
    expect(parseArxivId("2310.06825")).toBe("arxiv:2310.06825");
  });
  it("accepts an arxiv: prefix", () => {
    expect(parseArxivId("arxiv:2310.06825")).toBe("arxiv:2310.06825");
  });
  it("strips a version suffix", () => {
    expect(parseArxivId("2310.06825v3")).toBe("arxiv:2310.06825");
  });
  it("normalises an abs URL", () => {
    expect(parseArxivId("https://arxiv.org/abs/2310.06825v1")).toBe(
      "arxiv:2310.06825",
    );
  });
  it("normalises a pdf URL", () => {
    expect(parseArxivId("https://arxiv.org/pdf/2310.06825.pdf")).toBe(
      "arxiv:2310.06825",
    );
  });
  it("accepts old-style IDs", () => {
    expect(parseArxivId("cs.AI/0701001")).toBe("arxiv:cs.AI/0701001");
  });
  it("trims whitespace", () => {
    expect(parseArxivId("  2310.06825  ")).toBe("arxiv:2310.06825");
  });
  it("normalises a URL with a query string", () => {
    expect(parseArxivId("https://arxiv.org/abs/2310.06825?context=cs.LG")).toBe(
      "arxiv:2310.06825",
    );
  });
  it("accepts an upper-case V version suffix", () => {
    expect(parseArxivId("2310.06825V3")).toBe("arxiv:2310.06825");
  });
  it("accepts an upper-case ArXiv: prefix with version", () => {
    expect(parseArxivId("ArXiv:2310.06825v3")).toBe("arxiv:2310.06825");
  });
  it("rejects a non-id string", () => {
    expect(parseArxivId("not-an-id")).toBeNull();
  });
  it("rejects an empty string", () => {
    expect(parseArxivId("")).toBeNull();
  });
  it("rejects an under-length numeric id", () => {
    expect(parseArxivId("12.345")).toBeNull();
  });
});

const chunkServer = setupServer(
  http.get(`${API_BASE_URL}/chunks/42`, () =>
    HttpResponse.json({
      id: 42,
      paper_content_id: 7,
      section: "3.2 Routing",
      text: "Expert collapse is mitigated by load balancing.",
    }),
  ),
  http.get(`${API_BASE_URL}/chunks/999`, () =>
    HttpResponse.json({ detail: "no chunk 999" }, { status: 404 }),
  ),
);

describe("getChunk", () => {
  beforeAll(() => chunkServer.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => chunkServer.close());

  it("resolves a chunk id to its paper + text", async () => {
    const c = await getChunk(42);
    expect(c.paper_content_id).toBe(7);
    expect(c.text).toContain("Expert collapse");
    expect(c.section).toBe("3.2 Routing");
  });

  it("throws on 404", async () => {
    await expect(getChunk(999)).rejects.toThrow(/404/);
  });
});

// ── memories API ──────────────────────────────────────────────────────────────

const activeMemory: MemoryItem = {
  id: 1,
  scope: "session",
  session_id: 7,
  content: "User prefers concise answers.",
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
  status: "active",
  supersedes: null,
  superseded_by: null,
};
const supersededMemory: MemoryItem = {
  id: 2,
  scope: "session",
  session_id: 7,
  content: "User prefers verbose answers.",
  created_at: "2026-05-21T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
  status: "superseded",
  supersedes: null,
  superseded_by: 1,
};
const patchedMemory: MemoryItem = {
  ...activeMemory,
  id: 5,
  status: "superseded",
  superseded_by: 6,
};

// Capture variables for request-inspection tests.
let capturedPatchRequest: Request | undefined;
let capturedDeleteRequest: Request | undefined;

const memoriesServer = setupServer(
  http.get(`${API_BASE_URL}/memories`, ({ request }) => {
    const url = new URL(request.url);
    if (url.searchParams.get("session_id") === "7") {
      return HttpResponse.json([activeMemory, supersededMemory]);
    }
    return HttpResponse.json([]);
  }),
  http.patch(`${API_BASE_URL}/memories/5`, ({ request }) => {
    capturedPatchRequest = request.clone();
    return HttpResponse.json(patchedMemory);
  }),
  http.delete(`${API_BASE_URL}/memories/5`, ({ request }) => {
    capturedDeleteRequest = request.clone();
    return new HttpResponse(null, { status: 204 });
  }),
);

describe("listMemories", () => {
  beforeAll(() => memoriesServer.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => memoriesServer.close());

  it("fetches GET /memories?session_id=7 and returns both rows", async () => {
    const items = await listMemories(7);
    expect(items).toHaveLength(2);
    const [first, second] = items as [MemoryItem, MemoryItem];
    expect(first.id).toBe(1);
    expect(first.status).toBe("active");
    expect(first.scope).toBe("session");
    expect(second.id).toBe(2);
    expect(second.status).toBe("superseded");
    expect(second.superseded_by).toBe(1);
  });
});

describe("patchMemory", () => {
  beforeAll(() => memoriesServer.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => memoriesServer.close());

  it("sends PATCH with X-Paperhub-Session-Id header and returns updated item", async () => {
    const result = await patchMemory(5, { status: "superseded" }, 7);

    expect(result.id).toBe(5);
    expect(result.status).toBe("superseded");

    // Assert the captured request's method, header, and body.
    expect(capturedPatchRequest).toBeDefined();
    expect(capturedPatchRequest!.method).toBe("PATCH");
    expect(capturedPatchRequest!.headers.get("x-paperhub-session-id")).toBe("7");
    const body = (await capturedPatchRequest!.json()) as Record<string, unknown>;
    expect(body).toEqual({ status: "superseded" });
  });
});

describe("deleteMemory", () => {
  beforeAll(() => memoriesServer.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => memoriesServer.close());

  it("sends DELETE with X-Paperhub-Session-Id header and resolves", async () => {
    await expect(deleteMemory(5, 7)).resolves.toBeUndefined();

    expect(capturedDeleteRequest).toBeDefined();
    expect(capturedDeleteRequest!.method).toBe("DELETE");
    expect(capturedDeleteRequest!.headers.get("x-paperhub-session-id")).toBe("7");
  });
});

// ── createMemory ─────────────────────────────────────────────────────────────

const newMemory: MemoryItem = {
  id: 10,
  scope: "global",
  session_id: 7,
  content: "Prefers bullet-point summaries.",
  created_at: "2026-05-22T10:00:00Z",
  updated_at: "2026-05-22T10:00:00Z",
  status: "active",
  supersedes: null,
  superseded_by: null,
};

let capturedCreateRequest: Request | undefined;

const createMemoryServer = setupServer(
  http.post(`${API_BASE_URL}/memories`, ({ request }) => {
    capturedCreateRequest = request.clone();
    return HttpResponse.json(newMemory, { status: 201 });
  }),
);

describe("createMemory — success", () => {
  beforeAll(() => createMemoryServer.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => createMemoryServer.close());

  it("sends POST with body + X-Paperhub-Session-Id and returns the created item", async () => {
    const result = await createMemory(
      "Prefers bullet-point summaries.",
      "global",
      7,
    );

    expect(result.id).toBe(10);
    expect(result.scope).toBe("global");
    expect(result.status).toBe("active");

    expect(capturedCreateRequest).toBeDefined();
    expect(capturedCreateRequest!.method).toBe("POST");
    expect(capturedCreateRequest!.headers.get("x-paperhub-session-id")).toBe(
      "7",
    );
    const body = (await capturedCreateRequest!.json()) as Record<
      string,
      unknown
    >;
    expect(body).toEqual({
      content: "Prefers bullet-point summaries.",
      scope: "global",
    });
  });
});

describe("createMemory — 422 gate refusal", () => {
  const gateServer = setupServer(
    http.post(`${API_BASE_URL}/memories`, () =>
      HttpResponse.json(
        { detail: "sensitive personal information detected" },
        { status: 422 },
      ),
    ),
  );

  beforeAll(() => gateServer.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => gateServer.close());

  it("throws MemoryGateRefused with the server reason on 422", async () => {
    const err = await createMemory("my password is abc123", "session", 7).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(MemoryGateRefused);
    expect((err as MemoryGateRefused).reason).toMatch(
      /sensitive personal information/i,
    );
  });
});
