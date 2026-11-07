import { describe, expect, it } from "vitest";

import { cleanForPreview } from "@/lib/answerPreview";

describe("cleanForPreview", () => {
  it("turns a fenced sql block into an inline code span", () => {
    const text = "You have 61 papers.\n\n```sql\nSELECT COUNT(*)\nFROM paper_content\n```";
    expect(cleanForPreview(text)).toBe("You have 61 papers.\n\n`SELECT COUNT(*) FROM paper_content`");
  });

  it("handles a one-line fence and an unclosed one", () => {
    expect(cleanForPreview("a ```sql SELECT 1``` b")).toBe("a `SELECT 1` b");
    expect(cleanForPreview("a\n```sql\nSELECT 1")).toBe("a\nSELECT 1");
  });

  it("leaves plain text alone", () => {
    expect(cleanForPreview("Just text [chunk:3].")).toBe("Just text [chunk:3].");
  });
});
