import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PdfView } from "@/components/canvas/PdfView";

// react-pdf → plain divs exposing the width each page is rendered at.
vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: { workerSrc: "" } },
  Document: ({
    children,
    onLoadSuccess,
  }: {
    children: React.ReactNode;
    onLoadSuccess?: (pdf: { numPages: number }) => void;
  }) => {
    onLoadSuccess?.({ numPages: 2 });
    return <div>{children}</div>;
  },
  Page: ({ pageNumber, width }: { pageNumber: number; width?: number }) => (
    <div data-testid={`pdf-page-${pageNumber}`} data-width={width ?? "auto"} />
  ),
}));

// Controllable ResizeObserver + container width (jsdom does no layout).
let roCallbacks: (() => void)[] = [];
let clientWidth = 524;

beforeEach(() => {
  roCallbacks = [];
  clientWidth = 524;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(cb: () => void) {
        roCallbacks.push(cb);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(
    () => clientWidth,
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("PdfView width tracking", () => {
  it("keeps the last width while hidden (clientWidth 0)", async () => {
    render(<PdfView data={new Uint8Array([1, 2, 3])} />);
    await waitFor(() =>
      expect(screen.getByTestId("pdf-page-1")).toHaveAttribute("data-width", "500"),
    );

    // Chat hidden behind Ideas/Feedback → display:none → width 0.
    clientWidth = 0;
    act(() => roCallbacks.forEach((cb) => cb()));
    expect(screen.getByTestId("pdf-page-1")).toHaveAttribute("data-width", "500");

    // Shown again at a new width → tracks it.
    clientWidth = 424;
    act(() => roCallbacks.forEach((cb) => cb()));
    expect(screen.getByTestId("pdf-page-1")).toHaveAttribute("data-width", "400");
  });
});
