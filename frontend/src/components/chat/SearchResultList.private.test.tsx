import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SearchResultList } from "./SearchResultList";
import { useChatStore } from "@/store/chat";
import type { SearchResultCandidate } from "@/types/domain";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  attachFromLibrary: vi.fn(() =>
    Promise.resolve({
      papers_id: 11,
      paper_content_id: 42,
      cache_hit: true,
      title: "Secret Memo",
      is_private: true,
    }),
  ),
  // Never resolves: the assertion reads the OPTIMISTIC reference only.
  listSessionReferences: vi.fn(() => new Promise(() => {})),
}));

const candidate: SearchResultCandidate = {
  paper_id: "library:42",
  title: "Secret Memo",
  authors: [],
  year: null,
  abstract: null,
  arxiv_id: null,
  has_open_pdf: false,
  reason: "",
  finalize: false,
  auto_added: false,
  papers_id: null,
  error: null,
  already_in_session: false,
};

afterEach(() => useChatStore.getState().reset());

describe("SearchResultList add from library", () => {
  it("uses the server's is_private for the optimistic reference", async () => {
    render(<SearchResultList candidates={[candidate]} sessionId={7} />);
    await userEvent.click(screen.getByRole("button", { name: "Add as reference" }));
    const refs = useChatStore.getState().referencesBySession[7] ?? [];
    expect(refs).toHaveLength(1);
    expect(refs[0]!.is_private).toBe(true);
  });
});
