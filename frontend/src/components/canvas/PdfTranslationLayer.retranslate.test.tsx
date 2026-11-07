import { render, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PdfTranslationLayer, PdfTranslationPage } from "@/components/canvas/PdfTranslationLayer";
import { getPdfParagraphs, translateBlocks } from "@/lib/api";
import { useTranslationStore } from "@/store/translation";
import type { PdfParagraph, TranslateRequest } from "@/types/translation";

vi.mock("@/lib/api", () => ({
  getPdfParagraphs: vi.fn(),
  translateBlocks: vi.fn(),
}));

const mockedParagraphs = vi.mocked(getPdfParagraphs);
const mockedTranslate = vi.mocked(translateBlocks);

const ON_PAGE_1 = "p:/page/0/Text/1";
const ON_PAGE_2 = "p:/page/1/Text/2";
const PARAGRAPHS: PdfParagraph[] = [
  { key: ON_PAGE_1, text: "One.", math: [], fragments: [{ page: 0, bbox: [0, 0, 100, 20] }] },
  { key: ON_PAGE_2, text: "Two.", math: [], fragments: [{ page: 1, bbox: [0, 0, 100, 20] }] },
];

/** Every observed page is reported in view, except page 2. */
class InViewIO {
  constructor(private cb: IntersectionObserverCallback) {}
  observe(el: Element) {
    const inView = !el.closest('[data-testid="page-2"]');
    this.cb(
      [{ target: el, isIntersecting: inView } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
  disconnect() {}
  unobserve() {}
}

function Harness({
  nonce,
  onStaleChange,
}: {
  nonce: number;
  onStaleChange: (stale: boolean) => void;
}) {
  const readerRef = useRef<HTMLDivElement>(null);
  return (
    <div ref={readerRef}>
      <PdfTranslationLayer
        paperId={7}
        language="日本語"
        fullPaper
        enabled
        readerRef={readerRef}
        paragraphs={PARAGRAPHS}
        retranslateNonce={nonce}
        onStaleChange={onStaleChange}
      >
        <div data-testid="page-1">
          <PdfTranslationPage pageNumber={1} renderedWidth={300} originalWidth={600} />
        </div>
        <div data-testid="page-2">
          <PdfTranslationPage pageNumber={2} renderedWidth={300} originalWidth={600} />
        </div>
      </PdfTranslationLayer>
    </div>
  );
}

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", InViewIO);
  useTranslationStore.setState({ hintSeen: true });
  // Cached translations predate the terminology memory; a forced one doesn't.
  mockedTranslate.mockImplementation((req: TranslateRequest) =>
    Promise.resolve({
      results: req.blocks.map((b) => ({
        key: b.key,
        status: "ok" as const,
        text: `${req.force ? "new" : "old"}:${b.text}`,
        model: "m",
        stale_terminology: !req.force,
      })),
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("PdfTranslationLayer — stale terminology", () => {
  it("uses the caller's paragraphs, reports in-view staleness, re-translates with force", async () => {
    const stale = vi.fn();
    const { rerender } = render(<Harness nonce={0} onStaleChange={stale} />);
    await waitFor(() => expect(stale).toHaveBeenLastCalledWith(true));
    expect(mockedParagraphs).not.toHaveBeenCalled();
    expect(mockedTranslate.mock.calls.some(([r]) => r.force)).toBe(false);

    rerender(<Harness nonce={1} onStaleChange={stale} />);
    await waitFor(() => expect(stale).toHaveBeenLastCalledWith(false));
    const forced = mockedTranslate.mock.calls.map(([r]) => r).filter((r) => r.force);
    expect(forced).toHaveLength(1);
    // Only the in-view stale paragraph: page 2 is out of view.
    expect(forced[0]!.blocks.map((b) => b.key)).toEqual([ON_PAGE_1]);
  });
});
