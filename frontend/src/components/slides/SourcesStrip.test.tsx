import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { useCanvasStore } from "@/store/canvas";
import { SourcesStrip } from "./SourcesStrip";
import type { SlideSourceSection } from "@/types/domain";

const titleByPaperId = new Map<number, string>([[7, "Attention Is All You Need"]]);

describe("SourcesStrip", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useCanvasStore.setState({ requestedChunkId: null, requestNonce: 0, open: false });
  });

  it("renders a chip per cited section labelled paper + section", () => {
    const sources: SlideSourceSection[] = [
      { paper_id: 7, section_name: "Introduction", chunk_ids: [101, 102] },
    ];
    render(<SourcesStrip sources={sources} titleByPaperId={titleByPaperId} />);
    const chip = screen.getByRole("button", { name: /Introduction/ });
    expect(chip).toHaveTextContent("Attention Is All You Need");
    expect(chip).toHaveTextContent("Introduction");
  });

  it("opens the Citation Canvas spanning the section's first→last chunk", () => {
    const openSpy = vi.spyOn(useCanvasStore.getState(), "openCitation");
    const sources: SlideSourceSection[] = [
      { paper_id: 7, section_name: "Introduction", chunk_ids: [101, 102] },
    ];
    render(<SourcesStrip sources={sources} titleByPaperId={titleByPaperId} />);
    fireEvent.click(screen.getByRole("button", { name: /Introduction/ }));
    // First + last chunk → the canvas highlights the WHOLE cited section.
    expect(openSpy).toHaveBeenCalledWith(101, 102);
  });

  it("renders an unsourced cite muted + non-clickable", () => {
    const openSpy = vi.spyOn(useCanvasStore.getState(), "openCitation");
    const sources: SlideSourceSection[] = [
      { paper_id: 7, section_name: "Method", chunk_ids: [] },
    ];
    render(<SourcesStrip sources={sources} titleByPaperId={titleByPaperId} />);
    const chip = screen.getByText(/Method/);
    fireEvent.click(chip);
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("falls back to #<paper_id> when the title is unknown", () => {
    const sources: SlideSourceSection[] = [
      { paper_id: 42, section_name: "Results", chunk_ids: [1] },
    ];
    render(<SourcesStrip sources={sources} titleByPaperId={titleByPaperId} />);
    expect(screen.getByRole("button", { name: /Results/ })).toHaveTextContent("#42");
  });

  it("shows a quiet empty state when there are no sources", () => {
    render(<SourcesStrip sources={[]} titleByPaperId={titleByPaperId} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/no single source/i)).toBeInTheDocument();
  });
});
