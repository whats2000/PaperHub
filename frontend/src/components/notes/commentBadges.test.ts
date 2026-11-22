import { afterEach, describe, expect, it } from "vitest";

import type { NoteComment } from "@/types/notes";

import { lostCommentBadges, type BadgeView } from "./commentBadges";
import { createCaptionHosts, tableCaptions } from "./tableCaptions";
import { cleanupEditors, makeEditor } from "./testEditor";

afterEach(cleanupEditors);

const thread = (blockId: string): NoteComment => ({
  id: "c1",
  note_id: 7,
  block_id: blockId,
  quote: "gone",
  status: "open",
  created_at: "2026-11-16T00:00:00Z",
  items: [],
});

function host(testId: string) {
  const els = new Map<string, HTMLElement>();
  return (id: string) => {
    let el = els.get(id);
    if (!el) {
      el = document.createElement("span");
      el.dataset.testid = testId;
      els.set(id, el);
    }
    return el;
  };
}

function badgeView(blockId: string): () => BadgeView {
  const h = host("badge");
  return () => ({ threads: [thread(blockId)], badges: new Set([blockId]), host: h, onLost: () => {} });
}

const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const byTestId = (id: string) => document.querySelector(`[data-testid='${id}']`)!;

describe("the lost-comment badge of a table", () => {
  it("sits after the table and its notes, never inside the table", () => {
    makeEditor(
      [tableCaptions(createCaptionHosts()), lostCommentBadges(badgeView("t"))],
      [
        {
          id: "t",
          type: "table",
          content: { type: "tableContent", columnWidths: [null], headerRows: 1, rows: [{ cells: ["k"] }, { cells: ["v"] }] },
        },
        { id: "after", type: "paragraph", content: "after" },
      ],
    );
    const badge = byTestId("badge");
    expect(badge).toBeTruthy();
    expect(badge.closest("table")).toBeNull();
    const block = document.querySelector("[data-id='t']")!;
    expect(block.contains(badge)).toBe(true);
    expect(follows(block.querySelector("table")!, badge)).toBe(true);
    expect(follows(block.querySelector(".note-table-part-host:last-of-type")!, badge)).toBe(true);
  });
});
