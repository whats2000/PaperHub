/** Helpers for the PDF marks tests (the fake reader is `./pdfMarksReader`).
 * jsdom lays nothing out, so `layOut` gives the elements their boxes. */
import { screen } from "@testing-library/react";
import { vi } from "vitest";

import { CANVAS_DEFAULT_WIDTH, useCanvasStore } from "@/store/canvas";
import { useMarkNavStore } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkCreated, MarkIn, PaperPageBlock } from "@/types/notes";

export const BLOCKS: PaperPageBlock[] = [
  { block_id: "/page/0/Text/1", type: "Text", page: 0, box: [0.05, 0.05, 0.6, 0.1], text: "Robots learn from demos.", section: "1 Introduction" },
  { block_id: "/page/0/Text/2", type: "Text", page: 0, box: [0.05, 0.1, 0.6, 0.15], text: "Next line here.", section: "2 Method" },
];

/** Gives an element a client rect. */
export function place(el: Element, left: number, top: number, width: number, height: number): void {
  (el as HTMLElement).getBoundingClientRect = () => new DOMRect(left, top, width, height);
}

/** The reader 600 x 400, the page 600 x 800 at the origin, the spans. */
export function layOut(): void {
  place(screen.getByTestId("reader"), 0, 0, 600, 400);
  place(screen.getByTestId("page-1"), 0, 0, 600, 800);
  const spans: [string, number, number, number, number][] = [
    ["s1", 30, 40, 120, 16],
    ["s2", 150, 40, 150, 16],
    ["s3", 30, 90, 170, 16],
    ["s4", 30, 140, 170, 16],
    ["s5", 30, 190, 170, 16],
  ];
  for (const [id, ...box] of spans) {
    const el = screen.queryByTestId(id);
    if (el) place(el, ...box);
  }
}

function created(body: MarkIn): MarkCreated {
  const block = `nb_${body.section_key}`;
  return {
    mark: {
      id: `mk_${block}`,
      paper_content_id: body.paper_content_id,
      block_id: block,
      kind: body.kind,
      color: body.color,
      view: body.view,
      pieces: body.pieces,
      lang: null,
      source_ref: null,
      section_key: body.section_key,
      status: "ok",
      created_at: "2026-11-17T00:00:00Z",
      deleted_at: null,
    },
    block: null,
    saved_to: { note_id: 7, paper_title: "Duo", section: body.section_key, part_index: 0, part_count: 1 },
  };
}

export type CreateMock = ReturnType<typeof vi.fn<(body: MarkIn, opts?: { silent?: boolean }) => Promise<MarkCreated>>>;

/** Fresh stores: the paper's marks, a `create` that succeeds. */
export function resetStores(marks: Mark[] = []): CreateMock {
  const create: CreateMock = vi.fn((body: MarkIn) => Promise.resolve(created(body)));
  useMarksStore.setState({
    marksByPaper: { 3: marks },
    chunksByPaper: {},
    colors: [],
    showMarks: true,
    boxOn: false,
    penOn: false,
    penColor: "green",
    penSession: [],
    loadPaper: vi.fn(() => Promise.resolve()),
    loadColors: vi.fn(() => Promise.resolve()),
    create,
  });
  useNotesStore.setState({ caretAfter: null, byTarget: {}, quoteTarget: null });
  // The notes pane hidden, no note card: a click on a mark opens the card.
  useNotesPaneStore.setState({ open: false });
  useMarkNavStore.setState({ card: null, hovered: null });
  useCanvasStore.setState({ width: CANVAS_DEFAULT_WIDTH });
  return create;
}

/** jsdom has no layout: each span's box stands in for its characters. */
export function stubRangeGeometry(): void {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => new DOMRect(30, 40, 270, 16);
}

export function pdfMark(over: Partial<Mark>): Mark {
  return {
    id: "mk_1",
    paper_content_id: 3,
    block_id: "nb1",
    kind: "text", style: "highlight",
    color: "blue",
    view: "pdf",
    pieces: [],
    lang: null,
    source_ref: null,
    section_key: null,
    status: "ok",
    created_at: "2026-11-17T00:00:00Z",
    deleted_at: null,
    note_id: 7,
    ...over,
  };
}
