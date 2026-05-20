import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { parseArxivId, getChunk, API_BASE_URL } from "@/lib/api";

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
