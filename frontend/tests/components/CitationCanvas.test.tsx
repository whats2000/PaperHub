import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { useCanvasStore } from "@/store/canvas";
import { API_BASE_URL } from "@/lib/api";

const server = setupServer(
  http.get(`${API_BASE_URL}/chunks/42`, () =>
    HttpResponse.json({
      id: 42,
      paper_content_id: 7,
      section: "3.2 Routing",
      text: "Expert collapse is mitigated.",
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => useCanvasStore.getState().closeCanvas());

describe("CitationCanvas", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<CitationCanvas />);
    expect(container.firstChild).toBeNull();
  });

  it("opens, resolves the chunk, and points the iframe at the paper HTML", async () => {
    render(<CitationCanvas />);
    act(() => {
      useCanvasStore.getState().openCitation(42);
    });

    const iframe = await screen.findByTitle(/citation canvas/i);
    await waitFor(() =>
      expect(iframe).toHaveAttribute(
        "src",
        `${API_BASE_URL}/papers/content/7/html`,
      ),
    );
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
  });

  it("closes when the close button is clicked", async () => {
    render(<CitationCanvas />);
    act(() => {
      useCanvasStore.getState().openCitation(42);
    });
    await screen.findByTitle(/citation canvas/i);

    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(useCanvasStore.getState().open).toBe(false);
  });
});
