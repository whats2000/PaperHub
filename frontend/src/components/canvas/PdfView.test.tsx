import { render, waitFor } from "@testing-library/react";
import { useEffect, useLayoutEffect, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { PdfView } from "@/components/canvas/PdfView";

// How often each page's text layer was (re)built.
const textLayerRenders: Record<number, number> = {};

// react-pdf doesn't run under jsdom. The stand-in keeps the one behaviour
// this file is about: like react-pdf's TextLayer, a page rebuilds its text
// layer whenever `onRenderTextLayerSuccess` changes identity, then calls it.
vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {}, Util: { transform: () => [] } },
  Document: ({ children, onLoadSuccess }: { children: ReactNode; onLoadSuccess: (pdf: unknown) => void }) => {
    useEffect(() => onLoadSuccess({ numPages: 2 }), []); // eslint-disable-line react-hooks/exhaustive-deps
    return <div>{children}</div>;
  },
  Page: ({ pageNumber, onRenderTextLayerSuccess, children }: {
    pageNumber: number;
    onRenderTextLayerSuccess?: () => void;
    children: ReactNode;
  }) => {
    useLayoutEffect(() => {
      const n = (textLayerRenders[pageNumber] = (textLayerRenders[pageNumber] ?? 0) + 1);
      if (n < 50) onRenderTextLayerSuccess?.();
    }, [onRenderTextLayerSuccess, pageNumber]);
    return <div className="react-pdf__Page">{children}</div>;
  },
}));

describe("PdfView", () => {
  it("builds each page's text layer once, not again after every render", async () => {
    render(<PdfView data={new Uint8Array(8)} />);
    await waitFor(() => expect(textLayerRenders[2]).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 50));
    // A rebuilt text layer drops the reader's selection and every
    // text-anchored mark until it is drawn again.
    expect(textLayerRenders).toEqual({ 1: 1, 2: 1 });
  });
});
