import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";

import { SearchResultList } from "@/components/chat/SearchResultList";
import { useChatStore } from "@/store/chat";
import { API_BASE_URL } from "@/lib/api";
import type { ReferenceItem, SearchResultCandidate } from "@/types/domain";

function makeCandidate(
  overrides: Partial<SearchResultCandidate> = {},
): SearchResultCandidate {
  return {
    paper_id: "arxiv:1706.03762",
    title: "Attention Is All You Need",
    authors: ["Vaswani", "Shazeer", "Parmar"],
    year: 2017,
    abstract: "The dominant sequence transduction models...",
    arxiv_id: "1706.03762",
    has_open_pdf: true,
    reason: "Foundational transformer paper",
    finalize: false,
    auto_added: false,
    papers_id: null,
    error: null,
    already_in_session: false,
    ...overrides,
  };
}

function makeRef(overrides: Partial<ReferenceItem> = {}): ReferenceItem {
  return {
    papers_id: 1,
    paper_content_id: 1,
    enabled: true,
    added_at: "2024-01-01T00:00:00",
    arxiv_id: "1706.03762",
    title: "Attention Is All You Need",
    year: 2017,
    kind: "arxiv",
    ...overrides,
  };
}

const ingestResponse = {
  paper_content_id: 1,
  papers_id: 1,
  cache_hit: false,
  title: "Attention Is All You Need",
};

const sampleRefList: ReferenceItem[] = [makeRef()];

const server = setupServer(
  http.post(`${API_BASE_URL}/papers`, () =>
    HttpResponse.json(ingestResponse, { status: 201 }),
  ),
  http.get(`${API_BASE_URL}/papers`, () =>
    HttpResponse.json(sampleRefList),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  server.resetHandlers();
  useChatStore.getState().reset();
});

describe("SearchResultList", () => {
  it("renders nothing when candidates array is empty", () => {
    const { container } = render(
      <SearchResultList candidates={[]} sessionId={1} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders a card for each candidate with title, authors, year", () => {
    const candidates = [
      makeCandidate({ paper_id: "arxiv:1706.03762", title: "Paper A", year: 2017 }),
      makeCandidate({ paper_id: "arxiv:2005.14165", title: "Paper B", year: 2020 }),
    ];
    render(<SearchResultList candidates={candidates} sessionId={1} />);
    expect(screen.getByText("Paper A")).toBeInTheDocument();
    expect(screen.getByText("Paper B")).toBeInTheDocument();
    expect(screen.getByText("2017")).toBeInTheDocument();
    expect(screen.getByText("2020")).toBeInTheDocument();
  });

  it("shows 'Added by agent' badge when auto_added=true AND ref exists", () => {
    useChatStore.getState().setReferences(1, [makeRef({ papers_id: 42 })]);
    render(
      <SearchResultList
        candidates={[makeCandidate({ auto_added: true, papers_id: 42 })]}
        sessionId={1}
      />,
    );
    expect(screen.getByText(/added by agent/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add as reference/i })).toBeNull();
  });

  it("auto_added candidate falls back to 'Add as reference' when ref was removed", () => {
    // Backend originally set auto_added=true + papers_id=42, but the user
    // has since removed the paper from the panel — refs no longer contains
    // papers_id=42, so the card must reflect the live state.
    useChatStore.getState().setReferences(1, []);
    render(
      <SearchResultList
        candidates={[makeCandidate({ auto_added: true, papers_id: 42 })]}
        sessionId={1}
      />,
    );
    expect(screen.queryByText(/added by agent/i)).toBeNull();
    expect(
      screen.getByRole("button", { name: /add as reference/i }),
    ).toBeInTheDocument();
  });

  it("derives 'Added' badge via arxiv_id when papers_id is not on the candidate", () => {
    // User clicked Add on a previous render and we've refreshed refs since;
    // the candidate didn't carry papers_id, but the arxiv_id bridges identity.
    useChatStore.getState().setReferences(1, [
      makeRef({ papers_id: 7, arxiv_id: "1706.03762" }),
    ]);
    render(
      <SearchResultList
        candidates={[makeCandidate({ papers_id: null, arxiv_id: "1706.03762" })]}
        sessionId={1}
      />,
    );
    expect(screen.getByText(/^added$/i)).toBeInTheDocument();
  });

  it("library candidate already in session derives 'Added' from ref membership", () => {
    useChatStore.getState().setReferences(1, [
      makeRef({ papers_id: 99, arxiv_id: null, kind: "pdf_upload" }),
    ]);
    render(
      <SearchResultList
        candidates={[
          makeCandidate({
            paper_id: "library:5",
            already_in_session: true,
            papers_id: 99,
            arxiv_id: null,
            has_open_pdf: false,
          }),
        ]}
        sessionId={1}
      />,
    );
    expect(screen.getByText(/^added$/i)).toBeInTheDocument();
  });

  it("flips back to 'Add as reference' after the matching ref is removed mid-render", () => {
    useChatStore.getState().setReferences(1, [makeRef({ papers_id: 3 })]);
    render(
      <SearchResultList
        candidates={[makeCandidate({ papers_id: 3 })]}
        sessionId={1}
      />,
    );
    expect(screen.getByText(/^added$/i)).toBeInTheDocument();

    // Simulate the user removing the paper from ReferenceSourcesPanel.
    act(() => {
      useChatStore.getState().removeReferenceLocal(1, 3);
    });

    expect(screen.queryByText(/^added$/i)).toBeNull();
    expect(
      screen.getByRole("button", { name: /add as reference/i }),
    ).toBeInTheDocument();
  });

  it("shows 'Source unavailable' disabled button when error=no_ingestible_source", () => {
    render(
      <SearchResultList
        candidates={[makeCandidate({ error: "no_ingestible_source" })]}
        sessionId={1}
      />,
    );
    const btn = screen.getByRole("button", { name: /source unavailable/i });
    expect(btn).toBeDisabled();
  });

  it("calls POST /papers and shows 'Added' badge derived from optimistic ref insert", async () => {
    render(
      <SearchResultList
        candidates={[makeCandidate()]}
        sessionId={1}
      />,
    );
    const addBtn = screen.getByRole("button", { name: /add as reference/i });
    await userEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText(/^added$/i)).toBeInTheDocument();
    });

    // The optimistic insert (or the refresh roundtrip) populates refs so the
    // derivation flips to "Added" — no separate addedPaperIds slice needed.
    const refs = useChatStore.getState().referencesBySession[1] ?? [];
    expect(refs.some((r) => r.papers_id === ingestResponse.papers_id)).toBe(
      true,
    );
  });

  it("refreshes session references after successful add", async () => {
    render(
      <SearchResultList
        candidates={[makeCandidate()]}
        sessionId={1}
      />,
    );
    const addBtn = screen.getByRole("button", { name: /add as reference/i });
    await userEvent.click(addBtn);

    // After the add resolves, the store should have the fetched reference list
    await waitFor(() => {
      expect(
        useChatStore.getState().referencesBySession[1],
      ).toEqual(sampleRefList);
    });
  });

  it("sends candidate metadata in POST /papers body", async () => {
    // Intercept the POST and capture the parsed body.
    const capturedBodies: unknown[] = [];
    server.use(
      http.post(`${API_BASE_URL}/papers`, async ({ request }) => {
        capturedBodies.push(await request.json());
        return HttpResponse.json(ingestResponse, { status: 201 });
      }),
    );

    const candidate = makeCandidate({
      paper_id: "arxiv:1706.03762",
      title: "Attention Is All You Need",
      abstract: "The dominant sequence transduction models...",
      authors: ["Vaswani", "Shazeer", "Parmar"],
      year: 2017,
    });

    render(<SearchResultList candidates={[candidate]} sessionId={1} />);
    const addBtn = screen.getByRole("button", { name: /add as reference/i });
    await userEvent.click(addBtn);

    await waitFor(() => expect(capturedBodies.length).toBeGreaterThan(0));

    const body = capturedBodies[0] as Record<string, unknown>;
    expect(body.paper_id).toBe("arxiv:1706.03762");
    expect(body.title).toBe("Attention Is All You Need");
    expect(body.abstract).toBe("The dominant sequence transduction models...");
    expect(body.authors).toEqual(["Vaswani", "Shazeer", "Parmar"]);
    expect(body.year).toBe(2017);
  });
});
