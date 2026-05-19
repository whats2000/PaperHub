import { describe, it, expect } from "vitest";
import { parseArxivId } from "@/lib/api";

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
  it("rejects garbage", () => {
    expect(parseArxivId("not-an-id")).toBeNull();
    expect(parseArxivId("")).toBeNull();
    expect(parseArxivId("12.345")).toBeNull();
  });
});
