import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReferenceSourcesPanel } from "./ReferenceSourcesPanel";
import { useChatStore } from "@/store/chat";
import type { ReferenceItem } from "@/types/domain";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listSessionReferences: vi.fn(() => Promise.resolve([])),
}));

function ref(id: number, title: string, isPrivate: boolean, arxiv: string | null): ReferenceItem {
  return {
    papers_id: id,
    paper_content_id: id + 100,
    enabled: true,
    added_at: "2026-01-01",
    arxiv_id: arxiv,
    title,
    year: null,
    kind: "pdf_upload",
    is_private: isPrivate,
  };
}

beforeEach(() => {
  useChatStore.setState({
    sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
    activeSessionId: 1,
    referencesBySession: {
      7: [
        ref(1, "Secret Memo", true, null),
        ref(2, "Secret Arxiv", true, "2401.00001"),
        ref(3, "Open Paper", false, null),
      ],
    },
  });
});

describe("ReferenceSourcesPanel private marker", () => {
  it("shows a labelled lock on private rows only", () => {
    render(<ReferenceSourcesPanel frontendSessionId={1} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByLabelText("Private document")).toBeInTheDocument();
    expect(within(rows[1]!).getByLabelText("Private document")).toBeInTheDocument();
    expect(within(rows[2]!).queryByLabelText("Private document")).toBeNull();
  });

  it("makes the lock keyboard-focusable so its tooltip is reachable", () => {
    render(<ReferenceSourcesPanel frontendSessionId={1} />);
    const lock = within(screen.getAllByRole("listitem")[0]!).getByLabelText(
      "Private document",
    );
    expect(lock).toHaveAttribute("tabindex", "0");
    lock.focus();
    expect(lock).toHaveFocus();
  });
  it("renders the arXiv row's lock beside the link, not inside it", () => {
    render(<ReferenceSourcesPanel frontendSessionId={1} />);
    const row = screen.getAllByRole("listitem")[1]!;
    const lock = within(row).getByLabelText("Private document");
    expect(lock.closest("a")).toBeNull();
    // The link's name is the title alone; the lock is a sibling.
    expect(within(row).getByRole("link", { name: "Secret Arxiv" })).toBeInTheDocument();
  });
});
