import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { NotesChangeCard } from "@/components/chat/NotesChangeCard";
import { API_BASE_URL } from "@/lib/api";
import { useCanvasStore } from "@/store/canvas";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { NotesChangedData } from "@/types/domain";
import type { ChangeSetStatus } from "@/types/notes";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => server.resetHandlers());

const data: NotesChangedData = {
  change_set_id: "cs_1",
  note_id: 7,
  note_title: "Attention",
  summary: { added: 3, updated: 0, deleted: 0, suggested: 1, rejected: 0, section: "2 Background" },
  revision: 5,
};

function detail(over: Partial<ChangeSetStatus> = {}): ChangeSetStatus {
  return {
    id: "cs_1",
    note_id: 7,
    status: "applied",
    reapplied_by: null,
    head: { id: "cs_1", status: "applied" },
    note: { target_kind: "paper", target_id: "3", title: "Attention" },
    changes: [
      { kind: "added", block_id: "b1", text: "Network transmission dominates." },
      {
        kind: "suggested",
        block_id: "u1",
        op: "update",
        before: "latency drops up to 83%",
        text: "latency drops 89%, 88% and 83%",
        status: "open",
      },
    ],
    ...over,
  };
}

let calls: string[] = [];
beforeEach(() => {
  calls = [];
  useNotesPaneStore.setState({ open: false, activeTab: "reader", request: null });
  useNotesStore.setState({ notes: {}, byTarget: {}, reloads: {} });
});

describe("NotesChangeCard", () => {
  it("sums the change set up in one line, with plural counts", async () => {
    server.use(http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())));
    render(<NotesChangeCard data={data} />);
    expect(screen.getByText(/Added 3 notes under §2/)).toBeInTheDocument();
    expect(screen.getByText("1 suggestion on your text")).toBeInTheDocument();
    await screen.findByRole("button", { name: /Undo/ });
  });

  it("uses the singular for one note and names no section when there is none", () => {
    server.use(http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())));
    render(
      <NotesChangeCard
        data={{ ...data, summary: { ...data.summary, added: 1, suggested: 2, section: null } }}
      />,
    );
    expect(screen.getByText(/^Added 1 note$/)).toBeInTheDocument();
    expect(screen.getByText("2 suggestions on your text")).toBeInTheDocument();
  });

  it("counts an added table by its size instead of as a note", () => {
    server.use(http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())));
    render(
      <NotesChangeCard
        data={{
          ...data,
          summary: {
            ...data.summary,
            added: 2,
            suggested: 0,
            section: null,
            tables_added: [{ rows: 2, cols: 2 }],
          },
        }}
      />,
    );
    expect(screen.getByText("Added a table (2 rows × 2 columns)")).toBeInTheDocument();
    expect(screen.getByText(/^Added 1 note$/)).toBeInTheDocument();
  });

  it("names the paper section a table was added under", () => {
    server.use(http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())));
    render(
      <NotesChangeCard
        data={{
          ...data,
          summary: { ...data.summary, added: 1, suggested: 0, tables_added: [{ rows: 4, cols: 3 }] },
        }}
      />,
    );
    expect(screen.getByText("Added a table (4 rows × 3 columns) under §2")).toBeInTheDocument();
    expect(screen.queryByText(/Added \d+ note/)).not.toBeInTheDocument();
  });

  it("names the other notes a mark recolour redrew, on its one card (Task 16)", () => {
    server.use(http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())));
    render(
      <NotesChangeCard
        data={{
          ...data,
          summary: { ...data.summary, added: 0, updated: 1, suggested: 0, section: null },
          redrawn: [
            { note_id: 8, note_title: "Idea", revision: 3, change_set_id: "cs_8", blocks: ["q8"] },
            { note_id: 9, note_title: "Chat", revision: 2, change_set_id: "cs_9", blocks: ["q9", "q10"] },
          ],
        }}
      />,
    );
    expect(screen.getByText("Also updated the mark in 2 other notes: Idea, Chat")).toBeInTheDocument();
  });

  it("its Undo reloads the other notes the undo redrew back (Task 16)", async () => {
    const loads: number[] = [];
    useNotesStore.setState({
      notes: { 8: {} as never },
      loadNote: (id: number) => {
        loads.push(id);
        return Promise.resolve();
      },
    } as never);
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())),
      http.post(`${API_BASE_URL}/notes/change-sets/cs_1/undo`, () =>
        HttpResponse.json({
          note_id: 7, revision: 6, change_set_id: "cs_1", applied: ["q7"], suggested: [], rejected: [], reparented: [],
          marks: [{ mark_id: "mk_1", paper_id: 3, block_id: "q7", color: { from: "blue", to: "green" }, blocks: ["q8"] }],
          redrawn: [{ note_id: 8, revision: 4, change_set_id: "cs_10", blocks: ["q8"] }],
        }),
      ),
    );
    render(<NotesChangeCard data={{ ...data, redrawn: [{ note_id: 8, note_title: "Idea", revision: 3, change_set_id: "cs_8", blocks: ["q8"] }] }} />);
    fireEvent.click(await screen.findByRole("button", { name: /Undo/ }));
    await waitFor(() => expect(loads).toContain(8));
  });

  it("Undo turns into Apply again and back, each call on the set live at the time", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())),
      http.post(`${API_BASE_URL}/notes/change-sets/:id/undo`, ({ params }) => {
        calls.push(`undo:${String(params.id)}`);
        return HttpResponse.json({ note_id: 7, revision: 6, change_set_id: params.id, applied: ["b1"], suggested: [], rejected: [], reparented: [] });
      }),
      http.post(`${API_BASE_URL}/notes/change-sets/:id/reapply`, ({ params }) => {
        calls.push(`reapply:${String(params.id)}`);
        return HttpResponse.json({ note_id: 7, revision: 7, change_set_id: "cs_2", applied: ["b1"], suggested: [], rejected: [], reparented: [] });
      }),
    );
    render(<NotesChangeCard data={data} />);
    fireEvent.click(await screen.findByRole("button", { name: /Undo/ }));
    // In flight: the control is off, so a second click cannot race the first.
    expect(screen.getByTestId("notes-card-toggle")).toBeDisabled();
    fireEvent.click(await screen.findByRole("button", { name: /Apply again/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Undo/ }));
    await screen.findByRole("button", { name: /Apply again/ });
    expect(calls).toEqual(["undo:cs_1", "reapply:cs_1", "undo:cs_2"]);
    expect(screen.getByText(/Undone/)).toBeInTheDocument();
  });

  it("after a reload shows where the set stands now (Apply again when undone)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () =>
        HttpResponse.json(detail({ status: "undone", head: { id: "cs_1", status: "undone" } })),
      ),
    );
    render(<NotesChangeCard data={data} />);
    expect(await screen.findByRole("button", { name: /Apply again/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Open in notes/ })).toBeDisabled();
  });

  it("follows Apply again to the set live now, so Undo undoes that one", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () =>
        HttpResponse.json(detail({ status: "undone", reapplied_by: "cs_2", head: { id: "cs_2", status: "applied" } })),
      ),
      http.post(`${API_BASE_URL}/notes/change-sets/:id/undo`, ({ params }) => {
        calls.push(`undo:${String(params.id)}`);
        return HttpResponse.json({ note_id: 7, revision: 8, change_set_id: params.id, applied: [], suggested: [], rejected: [], reparented: [] });
      }),
    );
    render(<NotesChangeCard data={data} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Undo/ })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    await screen.findByRole("button", { name: /Apply again/ });
    expect(calls).toEqual(["undo:cs_2"]);
  });

  it("says when Undo kept blocks the user edited since", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())),
      http.post(`${API_BASE_URL}/notes/change-sets/cs_1/undo`, () =>
        HttpResponse.json({
          note_id: 7, revision: 6, change_set_id: "cs_1", applied: [], suggested: [], reparented: [],
          rejected: [{ block_id: "b1", reason: "changed_since_base", current: null }],
        }),
      ),
    );
    render(<NotesChangeCard data={data} />);
    fireEvent.click(await screen.findByRole("button", { name: /Undo/ }));
    expect(await screen.findByText(/1 note you edited since was kept/)).toBeInTheDocument();
  });

  it("Open in notes opens the pane at the note's first changed block", async () => {
    server.use(http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())));
    const openPaper = vi.spyOn(useCanvasStore.getState(), "openPaper");
    render(<NotesChangeCard data={data} />);
    const open = await screen.findByRole("button", { name: /Open in notes/ });
    await waitFor(() => expect(open).toBeEnabled());
    act(() => {
      fireEvent.click(open);
    });
    const pane = useNotesPaneStore.getState();
    expect(pane.open).toBe(true);
    expect(pane.activeTab).toBe("notes");
    expect(pane.request).toMatchObject({ kind: "paper", id: 3, noteId: 7, blockId: "b1" });
    expect(openPaper).toHaveBeenCalledWith(3);
  });

  it("Details folds the diff out and back", async () => {
    server.use(http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())));
    render(<NotesChangeCard data={data} />);
    const details = screen.getByRole("button", { name: /Details/ });
    expect(details).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(details);
    expect(details).toHaveAttribute("aria-expanded", "true");
    const region = screen.getByTestId("notes-card-details");
    expect(region).toHaveAttribute("data-state", "open");
    expect(await screen.findByText("Network transmission dominates.")).toBeInTheDocument();
    expect(screen.getByText("up to")).toBeInTheDocument();
    expect(screen.getByText("89%, 88% and")).toBeInTheDocument();
    fireEvent.click(details);
    expect(region).toHaveAttribute("data-state", "closed");
  });

  it("shows a table change cell by cell, as the suggestion bar does, never the table as one line", async () => {
    const cell = (text: string) => ({ type: "tableCell", props: {}, content: [{ type: "text", text, styles: {} }] });
    const table = (rows: string[][]) => ({
      type: "tableContent",
      headerRows: 1,
      rows: rows.map((r) => ({ cells: r.map(cell) })),
    });
    const before = table([["方法", "延遲"], ["RoboTTT", "固定"], ["LoRA", "待查"]]);
    const after = table([["方法", "延遲"], ["RoboTTT", "固定"], ["LoRA", "無"], ["Full", "無"]]);
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () =>
        HttpResponse.json(
          detail({
            changes: [
              {
                kind: "suggested",
                block_id: "t1",
                op: "update",
                before: "方法 | 延遲\nRoboTTT | 固定\nLoRA | 待查",
                text: "方法 | 延遲\nRoboTTT | 固定\nLoRA | 無\nFull | 無",
                status: "open",
                table: {
                  before: { props: { caption: "Old" }, content: before },
                  after: { props: { caption: "New" }, content: after },
                },
              },
            ],
          }),
        ),
      ),
    );
    render(<NotesChangeCard data={data} />);
    fireEvent.click(screen.getByRole("button", { name: /Details/ }));
    const cells = await screen.findAllByTestId("suggestion-cell");
    expect(cells.map((c) => c.textContent)).toEqual(["LoRA · 延遲: 待查 無", "New row: Full | 無"]);
    expect(screen.getByTestId("suggestion-part-caption")).toHaveTextContent("Table caption: Old New");
    // The unchanged row is not listed, and no line holds the whole table.
    expect(screen.getByTestId("notes-card-details").textContent).not.toContain("RoboTTT");
  });
});

describe("MessageBubble", () => {
  it("shows a replayed notes_changed event as the card, below the answer", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () =>
        HttpResponse.json(detail({ status: "undone", head: { id: "cs_1", status: "undone" } })),
      ),
    );
    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "I put three notes under §2.",
          run_id: 5,
          status: "ok",
          agent_events: [{ event: "notes_changed", data }],
        }}
      />,
    );
    const card = screen.getByTestId("notes-change-card");
    const answer = screen.getByText("I put three notes under §2.");
    expect(answer.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Its state is the server's, not the event's.
    expect(await screen.findByRole("button", { name: /Apply again/ })).toBeInTheDocument();
  });
});

describe("NotesChangeCard after Undo / Apply again", () => {
  it("refetches the Details after each Undo and Apply again", async () => {
    let gets = 0;
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => {
        gets += 1;
        return HttpResponse.json(
          gets === 1
            ? detail()
            : detail({ changes: [{ kind: "added", block_id: "b1", text: `fetch ${gets}` }] }),
        );
      }),
      http.post(`${API_BASE_URL}/notes/change-sets/:id/undo`, ({ params }) =>
        HttpResponse.json({ note_id: 7, revision: 6, change_set_id: params.id, applied: [], suggested: [], rejected: [], reparented: [] }),
      ),
      http.post(`${API_BASE_URL}/notes/change-sets/:id/reapply`, () =>
        HttpResponse.json({ note_id: 7, revision: 7, change_set_id: "cs_2", applied: [], suggested: [], rejected: [], reparented: [] }),
      ),
    );
    render(<NotesChangeCard data={data} />);
    fireEvent.click(screen.getByRole("button", { name: /Details/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Undo/ }));
    expect(await screen.findByText("fetch 2")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Apply again/ }));
    expect(await screen.findByText("fetch 3")).toBeInTheDocument();
  });

  it("an Apply again that put nothing back shows the failure and stays undone", async () => {
    const { toast } = await import("sonner");
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () =>
        HttpResponse.json(detail({ status: "undone", head: { id: "cs_1", status: "undone" } })),
      ),
      http.post(`${API_BASE_URL}/notes/change-sets/cs_1/reapply`, () =>
        HttpResponse.json({
          note_id: 7, revision: 7, change_set_id: null, applied: [], suggested: [], reparented: [],
          rejected: [{ block_id: "b1", reason: "changed_since_base", current: null }],
        }),
      ),
    );
    render(<NotesChangeCard data={data} />);
    fireEvent.click(await screen.findByRole("button", { name: /Apply again/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: /Apply again/ })).toBeEnabled();
    expect(screen.getByText(/Undone/)).toBeInTheDocument();
  });

  it("keeps the kept-notes line while Undone folds away, clears it on the next Undo", async () => {
    let undos = 0;
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () => HttpResponse.json(detail())),
      http.post(`${API_BASE_URL}/notes/change-sets/:id/undo`, ({ params }) => {
        undos += 1;
        return HttpResponse.json({
          note_id: 7, revision: 6, change_set_id: params.id, applied: [], suggested: [], reparented: [],
          rejected: undos === 1 ? [{ block_id: "b1", reason: "changed_since_base", current: null }] : [],
        });
      }),
      http.post(`${API_BASE_URL}/notes/change-sets/:id/reapply`, () =>
        HttpResponse.json({ note_id: 7, revision: 7, change_set_id: "cs_2", applied: ["b1"], suggested: [], rejected: [], reparented: [] }),
      ),
    );
    render(<NotesChangeCard data={data} />);
    fireEvent.click(await screen.findByRole("button", { name: /Undo/ }));
    expect(await screen.findByText(/1 note you edited since was kept/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Apply again/ }));
    await screen.findByRole("button", { name: /Undo/ });
    // Still there while the line folds away.
    expect(screen.getByText(/1 note you edited since was kept/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    await screen.findByRole("button", { name: /Apply again/ });
    await waitFor(() => expect(screen.queryByText(/edited since/)).toBeNull());
  });
});
