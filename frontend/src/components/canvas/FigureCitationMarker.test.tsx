import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { API_BASE_URL } from "@/lib/api";
import {
  buildFigureOrdinalMap,
  parseFigureKeys,
  parseViewKey,
} from "@/lib/figureCitations";
import { useCanvasStore } from "@/store/canvas";
import type { ChatMessage } from "@/types/domain";

const server = setupServer();
const requests: string[] = [];

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  requests.length = 0;
  server.resetHandlers(
    http.get(`${API_BASE_URL}/papers/content/:pid/*`, ({ request }) => {
      requests.push(request.url);
      return HttpResponse.json({});
    }),
    http.get(`${API_BASE_URL}/feedback/runs/:runId`, () => HttpResponse.json(null)),
    http.get(`${API_BASE_URL}/feedback/sessions/:id`, () => HttpResponse.json([])),
    http.get(`${API_BASE_URL}/feedback/runs`, () => HttpResponse.json([])),
  );
  useCanvasStore.setState({
    open: false,
    requestedChunkId: null,
    requestedFigure: null,
    requestedView: null,
    requestNonce: 0,
  });
});

describe("evidence citation parsing", () => {
  it("numbers figures and page regions together, in first-seen order", () => {
    const text =
      "a [fig:20/fig-003] b [view:20/5/120,340,560,720] c " +
      "[fig:53/fig-003, fig:20/fig-003] d [view: 20 / 5 / 120,340,560,720]";
    expect([...buildFigureOrdinalMap(text).entries()]).toEqual([
      ["20/fig-003", 1],
      ["view:20/5/120,340,560,720", 2],
      ["53/fig-003", 3],
    ]);
    expect(parseFigureKeys("20/fig-003, 20/fig-004")).toEqual([
      { paper: 20, figureId: "fig-003", key: "20/fig-003" },
      { paper: 20, figureId: "fig-004", key: "20/fig-004" },
    ]);
    expect(parseViewKey("20/5/120,340,560,720")).toEqual({
      paper: 20,
      page: 5,
      box: [120, 340, 560, 720],
      key: "view:20/5/120,340,560,720",
    });
  });
});

describe("evidence markers in an answer", () => {
  const msg: ChatMessage = {
    role: "assistant",
    content:
      "Four charts, the tallest about 77% [fig:20/fig-003]; the legend " +
      "reads AP50 [view:20/5/120,340,560,720], see also [chunk:12].",
    run_id: 9,
    status: "ok",
  };

  it("renders figure and region superscripts next to chunk citations", () => {
    render(<MessageBubble message={msg} backendSessionId={1} />);
    expect(screen.getByRole("button", { name: "figure citation 1" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "region on page 5, citation 2" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "citation 1" })).toBeInTheDocument();
    expect(screen.queryByText(/\[fig:|\[view:/)).toBeNull();
  });

  it("a figure marker opens the reader on that figure, with no popover", async () => {
    const user = userEvent.setup();
    render(<MessageBubble message={msg} backendSessionId={1} />);
    await user.click(screen.getByRole("button", { name: "figure citation 1" }));
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.requestedFigure).toEqual({ paper: 20, figureId: "fig-003" });
    expect(s.requestNonce).toBe(1);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(requests).toEqual([]); // the canvas resolves it, not the marker
  });

  it("a region marker opens the reader on that page region", async () => {
    const user = userEvent.setup();
    render(<MessageBubble message={msg} backendSessionId={1} />);
    await user.click(
      screen.getByRole("button", { name: "region on page 5, citation 2" }),
    );
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.requestedView).toEqual({ paper: 20, page: 5, box: [120, 340, 560, 720] });
    expect(s.requestedFigure).toBeNull();
  });
});
